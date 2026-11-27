"""생김새 특징값 (L2 정규화된 벡터).

- dinov2: facebook/dinov2-small (384차원). 같은 '그 물체'인지 비교하는 데 강함. config.REID_DINOV2_DIR 에서 읽음
- clip  : OpenAI CLIP ViT-B/32 (512차원). '어떤 종류'인지에 강함. 예전 방식(user_labels.json)과 같은 모델

둘 다 인터넷 없이 로컬 파일만 씁니다 (GPU 가 없으면 CPU). 입력은 augment.to_input 으로 만든 정사각형 BGR 사진.
"""
import numpy as np


BUCKETS = (1, 2, 4, 8, 16)


class _Base:
    name = "base"
    dim = 0
    mean = std = (0.0, 0.0, 0.0)
    batch = 16
    runtime = "torch"
    _ov = None

    def _module(self):
        """OpenVINO 로 바꿀 torch 모듈 (입력: 정규화된 (N,3,224,224) → 출력: (N,D))."""
        raise NotImplementedError

    def _use_openvino(self, folder, runtime):
        """OpenVINO(내장 그래픽 → CPU)로 빠르게. 결과가 PyTorch 와 같은지(코사인 0.99 이상) 확인하고, 다르면 쓰지 않음.
        상자 수를 1·2·4·8·16 중 하나로 맞춰 계산 → 그래픽 장치가 상자 수마다 다시 컴파일하지 않음."""
        if runtime == "torch":
            return
        try:
            import openvino as ov
            import torch
            folder.mkdir(parents=True, exist_ok=True)
            xml = folder / f"{self.name}.xml"
            core = ov.Core()
            core.set_property({"CACHE_DIR": str(folder / "cache")})
            if not xml.exists():
                with torch.no_grad():
                    om = ov.convert_model(self._module(), example_input=torch.zeros(2, 3, 224, 224),
                                          input=[ov.PartialShape([-1, 3, 224, 224])])
                ov.save_model(om, str(xml))
            want = {"openvino": ["GPU", "CPU"], "openvino_gpu": ["GPU"], "openvino_cpu": ["CPU"]}.get(runtime, ["CPU"])
            x = (torch.rand(2, 3, 224, 224, generator=torch.Generator().manual_seed(0)) * 4 - 2)
            with torch.inference_mode():
                ref = self._forward(x).float().numpy()
            for dev in want:
                if dev not in core.available_devices:
                    continue
                try:
                    cm = core.compile_model(str(xml), dev, {"PERFORMANCE_HINT": "LATENCY"})
                    out = self._ov_run(cm, x.numpy())
                    cos = (out * ref).sum(1) / (np.linalg.norm(out, axis=1) * np.linalg.norm(ref, axis=1))
                    if cos.min() < 0.99:
                        print(f"[경고] 재인식 특징 OpenVINO {dev} 결과가 PyTorch 와 다름 (코사인 {cos.min():.4f}) → 쓰지 않음")
                        continue
                    for b in BUCKETS:
                        cm(np.zeros((b, 3, 224, 224), np.float32))
                    self._ov, self.runtime = cm, f"openvino:{dev}"
                    return
                except Exception as e:
                    print(f"[경고] 재인식 특징 OpenVINO {dev} 실패: {e}")
        except Exception as e:
            print(f"[경고] OpenVINO 를 쓸 수 없어 PyTorch 로 재인식 특징을 계산합니다 ({e})")

    @staticmethod
    def _ov_run(cm, arr):
        outs = []
        for i in range(0, len(arr), BUCKETS[-1]):
            part = arr[i:i + BUCKETS[-1]]
            n = next(b for b in BUCKETS if b >= len(part))
            if n > len(part):
                part = np.concatenate([part, np.zeros((n - len(part), *part.shape[1:]), part.dtype)])
            outs.append(np.asarray(cm(part)[0])[:min(len(arr) - i, BUCKETS[-1])])
        return np.concatenate(outs).astype(np.float32)

    def _tensor(self, imgs):
        import torch
        x = np.stack([im[..., ::-1] for im in imgs]).astype(np.float32) / 255.0     # BGR → RGB
        x = (x - np.asarray(self.mean, np.float32)) / np.asarray(self.std, np.float32)
        return torch.from_numpy(x.transpose(0, 3, 1, 2).copy())

    def _forward(self, x):
        raise NotImplementedError

    def __call__(self, imgs) -> np.ndarray:
        import torch
        if not len(imgs):
            return np.zeros((0, self.dim), np.float32)
        if self._ov is not None:
            v = self._ov_run(self._ov, self._tensor(imgs).numpy())
            return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-8)
        outs = []
        with torch.inference_mode():
            for i in range(0, len(imgs), self.batch):
                outs.append(self._forward(self._tensor(imgs[i:i + self.batch])).float().numpy())
        v = np.concatenate(outs).astype(np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-8)


class DinoV2Embedder(_Base):
    name = "dinov2-small"
    dim = 384
    mean, std = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)

    def __init__(self, path, runtime="torch"):
        from pathlib import Path
        from transformers import AutoModel
        if not (Path(path) / "config.json").exists():
            raise FileNotFoundError(
                f"DINOv2 모델이 없습니다: {path}\n"
                "  처음 한 번: python -c \"from huggingface_hub import snapshot_download as s; "
                "s('facebook/dinov2-small', local_dir=r'" + str(path) + "', allow_patterns=['*.json','*.safetensors'])\"")
        self.model = AutoModel.from_pretrained(str(path), local_files_only=True).eval()
        self._use_openvino(Path(path) / "openvino", runtime)

    def _module(self):
        import torch

        class Wrap(torch.nn.Module):
            def __init__(self, m):
                super().__init__()
                self.m = m

            def forward(self, x):
                return self.m(pixel_values=x).pooler_output

        return Wrap(self.model).eval()

    def _forward(self, x):
        return self.model(pixel_values=x).pooler_output          # CLS 토큰 (layernorm 후)


class ClipEmbedder(_Base):
    name = "clip-b32"
    dim = 512
    mean, std = (0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711)

    def __init__(self, root, runtime="torch"):
        from pathlib import Path
        import clip
        self.model, _ = clip.load("ViT-B/32", device="cpu", download_root=str(root))
        self.model = self.model.float().eval()
        self._use_openvino(Path(root) / "openvino", runtime)

    def _module(self):
        return self.model.visual.float().eval()

    def _forward(self, x):
        return self.model.encode_image(x)


def make_embedder(kind=None):
    import config
    kind = kind or config.REID_EMBEDDER
    rt = getattr(config, "REID_RUNTIME", "torch")
    if kind == "dinov2":
        return DinoV2Embedder(config.REID_DINOV2_DIR, rt)
    if kind == "clip":
        return ClipEmbedder(config.REID_CLIP_DIR, rt)
    raise ValueError(f"REID_EMBEDDER 는 'dinov2' 또는 'clip' 이어야 합니다: {kind!r}")

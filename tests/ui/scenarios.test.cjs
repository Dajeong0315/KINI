// No npm install required: node --test tests/ui/scenarios.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function model() {
  const context = vm.createContext({ setTimeout, fetch() { throw Error('Network forbidden'); } });
  for (const file of ['scenario_demo.js', 'api_client.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static', file), 'utf8'), context);
  }
  return { m: context.KINIDemo.createModel({ now: () => new Date('2026-11-25T12:00:00+09:00') }), context };
}
function cooking() { const { m } = model(); m.recommend(); m.pickRecipe(1); m.startCooking(true); return m; }
function save(m, extra = {}) { return m.saveRecord({ portions: [{ memberId: 1, servings: 1 }, { memberId: 2, servings: .5 }], confirmed: true, ...extra }); }
test('1: minimal family setup, confirm and stable member IDs', () => {
  const {m} = model(); m.saveHousehold({name:'테스트 가족',members:[{name:'첫째'},{name:'둘째',usual:.5}]});
  const s=m.snapshot(); assert.equal(s.household.configured,true); assert.equal(s.household.members[1].usual,.5);
  assert.deepEqual(Array.from(s.selected),[1,2]);
});
test('1: reject empty/duplicate family members and return isolated snapshots', () => {
  const {m}=model(); assert.throws(()=>m.saveHousehold({name:'가족',members:[]}));
  assert.throws(()=>m.saveHousehold({name:'가족',members:[{name:'가'},{name:'가'}]}));
  const s=m.snapshot();s.household.name='changed';assert.notEqual(m.snapshot().household.name,'changed');
});
test('2: first three and more pages never repeat until explicitly re-requested',()=>{
  const {m}=model();const first=m.recommend().result.items.map((r)=>r.id);
  const next=m.recommend({more:true}).result.items.map((r)=>r.id);assert.equal(first.length,3);assert.equal(next.length,3);
  assert.equal(next.some((id)=>first.includes(id)),false);
  assert.equal(m.recommend({more:true}).result.status,'no_safe_recommendation');
  assert.equal(m.recommend({more:true}).result.status,'no_safe_recommendation');
  assert.equal(m.recommend().result.items.length,3);
});
test('2: participant selection and changed conditions invalidate old menu',()=>{
  const {m}=model();m.recommend();m.pickRecipe(1);m.setConditions({time:15});
  assert.equal(m.snapshot().picked,null);assert.throws(()=>m.startCooking(true));
  assert.ok(m.recommend().result.items.every((r)=>r.minutes<=15));
  m.selectMembers([]);assert.throws(()=>m.recommend(),/참여자/);
});
test('2: explicit example exclusions remove ingredient and aliases',()=>{
  const {m}=model();m.saveHousehold({name:'가족',members:[{name:'예시',excluded:['계란']}]});
  const result=m.recommend().result;
  assert.ok(result.items.every((r)=>!r.ingredients.some((i)=>i.id==='egg')));
  assert.ok(result.excluded.some((r)=>r.reason.includes('제외')));
});
test('2: concept taste filters and unreachable conditions give no candidates',()=>{
  const {m}=model();m.setConditions({method:'concept',taste:'고소'});
  assert.ok(m.recommend().result.items.every((r)=>r.taste==='고소'));
  m.setConditions({time:5});assert.equal(m.recommend().result.status,'no_safe_recommendation');
});
test('2: error, privacy, and stale states remove previous candidates',()=>{
  const {m}=model();for(const scenario of ['api_error','privacy_blocked','stale_unvalidated','no_safe_recommendation']){
    m.recommend();m.pickRecipe(1);const s=m.recommend({scenario});assert.equal(s.result.status,scenario);assert.equal(s.result.items.length,0);assert.equal(s.picked,null);assert.throws(()=>m.startCooking(true));
  }
});
test('3: require candidate confirmation and retain participants during cooking',()=>{
  const {m}=model();m.recommend();m.pickRecipe(1);assert.throws(()=>m.startCooking(false));
  m.startCooking(true);assert.throws(()=>m.selectMembers([3]));assert.throws(()=>m.setConditions({time:15}));
  assert.equal(m.snapshot().cooking.people.length,2);
});
test('3: ingredient change recalculates nutrition, removal, rollback on validation failure',()=>{
  const m=cooking(),before=m.snapshot().cooking.nutrition.protein_g;
  m.changeIngredient('tofu',100);assert.ok(m.snapshot().cooking.nutrition.protein_g<before);
  const snap=JSON.stringify(m.snapshot().cooking);assert.throws(()=>m.changeIngredient('tofu',9999));assert.equal(JSON.stringify(m.snapshot().cooking),snap);
  m.changeIngredient('soy',0);assert.equal(m.snapshot().cooking.ingredients.some((i)=>i.id==='soy'),false);
});
test('3: no implicit unit conversion and questions do not silently change recipe',()=>{
  const m=cooking();const old=JSON.stringify(m.snapshot().cooking.ingredients);
  assert.equal(m.ask('두부 반 모만 넣을게').grams,null);assert.equal(m.ask('두부 100g 넣을게').grams,100);
  assert.equal(m.ask('조리 끝났어').intent,'complete');assert.equal(JSON.stringify(m.snapshot().cooking.ingredients),old);
  assert.throws(()=>m.ask(''));assert.throws(()=>m.ask('a'.repeat(501)));
});
test('3: steps clamp and cancelling does not consume inventory',()=>{
  const m=cooking(),before=JSON.stringify(m.snapshot().inventory);m.step(-100);assert.equal(m.snapshot().cooking.step,0);
  m.step(100);assert.equal(m.snapshot().cooking.step,3);m.cancelCooking();assert.equal(JSON.stringify(m.snapshot().inventory),before);assert.equal(m.snapshot().cooking,null);
});
test('4: final review and actual intake confirmation required before save',()=>{
  const m=cooking();assert.throws(()=>save(m));m.reviewCooking();assert.throws(()=>save(m,{confirmed:false}));
  assert.equal(m.snapshot().records.length,0);
});
test('4: changes saved, inventory consumed once, report based on actual portions',()=>{
  const m=cooking();m.changeIngredient('tofu',100);m.reviewCooking();save(m);
  const s=m.snapshot();assert.equal(s.records.length,1);assert.equal(s.cooking.status,'completed');
  assert.equal(s.records[0].ingredients.find((i)=>i.id==='tofu').grams,100);
  assert.equal(s.inventory.find((i)=>i.ingredient==='tofu').grams,700);
  const stock=JSON.stringify(s.inventory);assert.throws(()=>save(m));assert.equal(JSON.stringify(m.snapshot().inventory),stock);
  assert.ok(Math.abs(m.report({memberId:2}).totals.protein_g*2-m.report({memberId:1}).totals.protein_g)<.02);
});
test('4: duplicate/unknown participants, nonfinite and excess servings are rejected atomically',()=>{
  const m=cooking();m.reviewCooking();const old=JSON.stringify(m.snapshot());
  for(const portions of [[{memberId:1,servings:1},{memberId:1,servings:1}],[{memberId:1,servings:1},{memberId:99,servings:1}],[{memberId:1,servings:NaN},{memberId:2,servings:1}],[{memberId:1,servings:2},{memberId:2,servings:2}],[{memberId:1,servings:0},{memberId:2,servings:0}]])assert.throws(()=>save(m,{portions}));
  assert.equal(JSON.stringify(m.snapshot()),old);
});
test('4: person who did not eat can be recorded as zero',()=>{
  const m=cooking();m.reviewCooking();save(m,{portions:[{memberId:1,servings:1},{memberId:2,servings:0}]});
  assert.equal(m.report({memberId:2}).count,0);assert.equal(m.report({memberId:2}).totals.energy_kcal,0);
});
test('4: already-eaten direct record does not consume stock or modify portions by health goal',()=>{
  const {m}=model(),before=JSON.stringify(m.snapshot().inventory);save(m,{directRecipeId:2,method:'photo_manual'});
  assert.equal(JSON.stringify(m.snapshot().inventory),before);assert.equal(m.snapshot().records[0].portions[1].servings,.5);
});
test('5: simulated recognition only proposes, requires manual mass for uncertainty',()=>{
  const {m}=model(),before=JSON.stringify(m.snapshot().inventory);assert.equal(m.recognition('matched').grams,200);assert.equal(m.recognition('uncertain').grams,null);assert.equal(JSON.stringify(m.snapshot().inventory),before);
  assert.throws(()=>m.saveInventory({name:'당근',grams:null}));
});
test('5: unmatched food remains provisional and has no nutrition match',()=>{
  const {m}=model();const candidate=m.recognition('unknown');m.saveInventory({...candidate,grams:300});
  const row=m.snapshot().inventory.at(-1);assert.equal(row.confirmed,false);assert.equal(row.ingredient,null);
  assert.equal(m.catalog().some((i)=>i.name===row.name),false);
});
test('5: invalid dates and negative mass rejected, stock edits invalidate recommendation',()=>{
  const {m}=model();for(const props of [{grams:-1},{grams:10,useBy:'2026-02-30'},{grams:10,useBy:'not-a-date'}])assert.throws(()=>m.saveInventory({name:'당근',...props}));
  m.recommend();m.pickRecipe(1);m.saveInventory({name:'당근',grams:200});assert.equal(m.snapshot().result,null);
});
test('5: expired ingredients excluded, purchase permission cannot bypass start validation',()=>{
  const {m}=model();const row=m.snapshot().inventory.find((r)=>r.ingredient==='spinach');m.saveInventory({...row,uid:row.uid,name:'시금치',grams:0});
  m.setConditions({purchase:true});m.recommend();m.pickRecipe(1);assert.throws(()=>m.startCooking(true),/부족/);
  m.setConditions({purchase:false});const result=m.recommend().result;assert.ok(result.items.every((r)=>!r.ingredients.some((i)=>i.id==='spinach')));
  m.saveInventory({name:'시금치',grams:500,useBy:'2020-01-01'});assert.ok(m.recommend().result.items.every((r)=>!r.ingredients.some((i)=>i.id==='spinach')));
});
test('6: empty report contains real zeroes, today and week filters',()=>{
  const {m}=model();assert.equal(m.report().count,0);assert.equal(m.report().daily.length,7);assert.equal(m.report({days:1}).daily.length,1);
  save(m,{directRecipeId:1,eatenAt:'2026-11-20T10:00:00+09:00'});assert.equal(m.report({days:1}).count,0);assert.equal(m.report({days:7}).records.length,1);
});
test('6: supplement schedule, daily marking, next goal, and day closing',()=>{
  const {m}=model();assert.throws(()=>m.addSupplement({name:'예시',time:'25:00'}));
  m.addSupplement({name:'예시 보조식품',time:'09:00'});const id=m.snapshot().supplements[0].id;m.toggleSupplement(id);assert.equal(m.snapshot().supplements[0].takenDates.length,1);m.toggleSupplement(id);assert.equal(m.snapshot().supplements[0].takenDates.length,0);
  m.setNextGoal('채소 섭취');assert.ok(m.recommend().result.items[0].reasons.some((r)=>r.includes('채소 섭취')));
  m.closeDay();m.closeDay();assert.equal(m.snapshot().closedDays.length,1);save(m,{directRecipeId:1});assert.equal(m.snapshot().closedDays.length,0);
});
test('recorded member identities cannot be overwritten through setup',()=>{
  const {m}=model();save(m,{directRecipeId:1});assert.throws(()=>m.saveHousehold({name:'변경',members:[{name:'다른 사람'}]}));assert.throws(()=>m.loadExampleHousehold());
});
test('adapter runs every scenario without network or persistent browser storage',async()=>{
  const {context}=model(),api=context.KINIAPI.createClient();await api.call('recommend');await api.call('pickRecipe',1);await api.call('startCooking',true);await api.call('reviewCooking');await api.call('saveRecord',{portions:[{memberId:1,servings:1},{memberId:2,servings:.5}],confirmed:true});assert.equal(api.read().records.length,1);await api.call('reset');assert.equal(api.read().records.length,0);
});
test('optional diabetes status and meal precautions survive profile confirmation',()=>{
  const {m}=model();
  m.saveHousehold({name:'합성 가족',members:[{name:'예시1',diabetesStatus:'yes',mealPrecautions:'예시 주의사항',usual:.75},{name:'예시2',diabetesStatus:'no'},{name:'예시3'}]});
  const people=m.snapshot().household.members;
  assert.equal(people[0].diabetesStatus,'yes');assert.equal(people[0].mealPrecautions,'예시 주의사항');
  assert.equal(people[1].diabetesStatus,'no');assert.equal(people[2].diabetesStatus,'unspecified');
  assert.equal(people[0].usual,.75);
});
test('health status adds generic limitation warning without exposing diagnosis or raw notes in recommendations',()=>{
  const {m}=model();
  m.saveHousehold({name:'합성 가족',members:[{name:'예시',diabetesStatus:'yes',mealPrecautions:'PRIVATE_TEST_NOTE',usual:.75}]});
  const result=m.recommend().result;
  assert.ok(result.items.every((r)=>r.warnings.some((w)=>w.includes('건강 조건에 맞춘 식단을 검증하지 않습니다'))));
  assert.equal(JSON.stringify(result).includes('PRIVATE_TEST_NOTE'),false);
  assert.equal(JSON.stringify(result).includes('diabetesStatus'),false);
  m.pickRecipe(result.items[0].id);m.startCooking(true);
  assert.equal(m.snapshot().cooking.people[0].usual,.75);
  assert.equal(m.snapshot().cooking.servings,1);
});
test('health field values and note length are validated before profile update',()=>{
  const {m}=model(),before=JSON.stringify(m.snapshot());
  for(const props of [{diabetesStatus:'diagnosed_by_ai'},{mealPrecautions:'x'.repeat(201)}]){
    assert.throws(()=>m.saveHousehold({name:'합성 가족',members:[{name:'예시',...props}]}));
    assert.equal(JSON.stringify(m.snapshot()),before);
  }
});

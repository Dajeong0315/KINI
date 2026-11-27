bind = '127.0.0.1:5101'
workers = 1
worker_class = 'gthread'
threads = 4
timeout = 25
graceful_timeout = 22
keepalive = 2
preload_app = False
# Never log paths, query strings, headers, bodies, credentials or tracebacks.
accesslog = None
errorlog = '/dev/null'
capture_output = False
limit_request_line = 2048
limit_request_fields = 32
limit_request_field_size = 4096

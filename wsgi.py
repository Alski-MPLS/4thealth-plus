import os
from app import create_app
from dotenv import load_dotenv

load_dotenv()

app = create_app()

try:
    _proxy_count = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
except ValueError:
    import logging as _logging

    _logging.warning("TRUSTED_PROXY_COUNT is not a valid integer; ProxyFix not applied")
    _proxy_count = 0
if _proxy_count > 0:
    from werkzeug.middleware.proxy_fix import ProxyFix

    app = ProxyFix(app, x_for=_proxy_count, x_proto=_proxy_count, x_host=_proxy_count)

if __name__ == "__main__":
    cert = os.environ.get("SSL_CERT", "certs/cert.pem")
    key = os.environ.get("SSL_KEY", "certs/key.pem")
    port = int(os.environ.get("PORT", "5443"))

    ssl_ctx = None
    if os.path.exists(cert) and os.path.exists(key):
        import ssl

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cert, key)

    app.run(host="0.0.0.0", port=port, debug=False, ssl_context=ssl_ctx)

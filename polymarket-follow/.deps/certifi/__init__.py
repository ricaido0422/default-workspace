import ssl, os
def where():
    p = ssl.get_default_verify_paths()
    return p.cafile or p.openssl_cafile or os.path.join(os.path.dirname(__file__), "cacert.pem")

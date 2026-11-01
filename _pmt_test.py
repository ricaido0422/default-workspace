import urllib.request, json, sys
proxy = urllib.request.ProxyHandler({'https': 'http://127.0.0.1:7897', 'http': 'http://127.0.0.1:7897'})
opener = urllib.request.build_opener(proxy)
urllib.request.install_opener(opener)
try:
    resp = urllib.request.urlopen(
        'https://api.github.com/repos/HarrierOnChain/Prediction-Markets-Trading-Bot-Toolkits/releases/latest',
        timeout=20)
    data = json.loads(resp.read())
    print('OK tag:', data['tag_name'], '| name:', data.get('name'))
    print('=== assets ===')
    for a in data['assets']:
        print(a['name'], '|', round(a['size']/1048576, 2), 'MB |', a['browser_download_url'])
except Exception as e:
    print('ERR:', type(e).__name__, e)
    sys.exit(1)

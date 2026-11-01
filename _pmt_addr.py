import urllib.request, json, sys
proxy = urllib.request.ProxyHandler({'https': 'http://127.0.0.1:7897', 'http': 'http://127.0.0.1:7897'})
urllib.request.install_opener(urllib.request.build_opener(proxy))

addr = '0xCa45be78f628E97C06cdEdAc52950daADa4B34Cc'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://polymarket.com',
    'Referer': 'https://polymarket.com/',
}

def query(url, label):
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=20)
        data = json.loads(resp.read())
        print(f'=== {label} ===')
        print(json.dumps(data, indent=2, ensure_ascii=False)[:2500])
        print()
    except Exception as e:
        print(f'{label} ERR: {type(e).__name__}: {e}')

query(f'https://data-api.polymarket.com/v2/user-stats?user={addr}', 'user-stats')
query(f'https://data-api.polymarket.com/v2/positions?user={addr}&limit=10', 'positions')
query(f'https://data-api.polymarket.com/v2/value?user={addr}', 'portfolio-value')

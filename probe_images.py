"""Quick probe: what URLs does Tavily image search return, and do they
redirect on HEAD?"""
import os, sys, time
sys.path.insert(0, '.')
from backend import live_research
import requests

print('TAVILY configured:', bool(os.getenv('TAVILY_API_KEY', '').strip()))

for q in ['phone stand', 'pet bed', 'desk lamp', 'kitchen organizer', 'cleaning brush']:
    print('\n=== ' + q + ' ===')
    t0 = time.monotonic()
    urls = live_research.research_images(q + ' product photo', max_results=5)
    elapsed = time.monotonic() - t0
    print('  research_images returned ' + str(len(urls)) + ' url(s) in ' + f'{elapsed:.2f}s')
    for u in urls[:3]:
        print('    ' + u[:100])
        try:
            t = time.monotonic()
            r = requests.head(u, timeout=3.0, allow_redirects=False)
            ct = (r.headers.get('Content-Type') or '?')[:30]
            loc = (r.headers.get('Location') or '')[:50]
            print(f'      HEAD (no-redirect): {r.status_code} ct={ct} loc={loc} ({time.monotonic()-t:.2f}s)')
            if r.status_code in (301, 302, 303, 307, 308):
                t = time.monotonic()
                r2 = requests.head(u, timeout=3.0, allow_redirects=True)
                ct2 = (r2.headers.get('Content-Type') or '?')[:30]
                print(f'      HEAD (followed): {r2.status_code} ct={ct2} url={r2.url[:80]} ({time.monotonic()-t:.2f}s)')
        except Exception as e:
            print(f'      HEAD error: {type(e).__name__}: {e}')

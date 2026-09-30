#!/usr/bin/env python3
"""层级D：curl + beautifulsoup4 提取图片
用法: python3 extract_images_bs4.py <url> [搜索关键词]
输出: JSON 数组，每项 {url, alt, width, height}
"""

import sys, json, re, ssl
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import urllib.request

def extract(url, keyword=""):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    })
    html = urllib.request.urlopen(req, timeout=10, context=ctx).read()
    soup = BeautifulSoup(html, 'html.parser')
    
    images = []
    for img in soup.find_all('img'):
        # 取 src / data-src（懒加载）/ data-lazy-src
        src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
        if not src:
            continue
        
        full_url = urljoin(url, src.strip())
        alt = (img.get('alt') or '').strip()
        w = int(img.get('width', 0) or 0)
        h = int(img.get('height', 0) or 0)
        
        # 过滤小图（宽高都有值且都小于100）
        if w and h and w < 100 and h < 100:
            continue
        # 过滤空alt且宽或高小于100的
        if not alt and ((w and w < 100) or (h and h < 100)):
            continue
        
        # 过滤 logo/icon/avatar/favicon
        check = (full_url + alt).lower()
        if re.search(r'logo|icon|avatar|favicon|pixel|tracking|badge', check):
            continue
        
        images.append({'url': full_url, 'alt': alt, 'width': w, 'height': h})
    
    # 优先 alt 含关键词的
    if keyword:
        images.sort(key=lambda x: keyword.lower() in x['alt'].lower(), reverse=True)
    
    return images[:5]

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: extract_images_bs4.py <url> [keyword]'}))
        sys.exit(1)
    
    url = sys.argv[1]
    keyword = sys.argv[2] if len(sys.argv) > 2 else ""
    
    try:
        imgs = extract(url, keyword)
        print(json.dumps(imgs, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({'error': str(e)[:200]}, ensure_ascii=False))
        sys.exit(1)

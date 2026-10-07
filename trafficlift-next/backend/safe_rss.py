"""Bounded RSS parsing with DTDs and entity declarations disabled."""
import re
import xml.etree.ElementTree as ET


def parse_rss(data):
    if not isinstance(data,bytes) or len(data)>2_000_000:
        raise ValueError('RSS response exceeds limit.')
    # Normalize encoding before checking declarations, including UTF-16 feeds.
    if data.startswith((b'\xff\xfe',b'\xfe\xff')):
        text=data.decode('utf-16')
    elif b'\x00' in data:
        raise ValueError('Unsupported RSS encoding.')
    else:
        text=data.decode('utf-8-sig')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b',text,re.I):
        raise ValueError('RSS declarations are not permitted.')
    root=ET.fromstring(text)
    stack=[(root,0)];count=0
    while stack:
        node,depth=stack.pop();count+=1
        if count>20000 or depth>40:raise ValueError('RSS structure exceeds limit.')
        stack.extend((child,depth+1) for child in node)
    return root

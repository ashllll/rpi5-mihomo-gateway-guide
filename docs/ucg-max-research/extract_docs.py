import re,json,html,sys,urllib.request
UA={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131 Safari/537.36"}
def flight_text(url):
    req=urllib.request.Request(url,headers=UA)
    s=urllib.request.urlopen(req,timeout=30).read().decode('utf-8','ignore')
    chunks=re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', s)
    texts=[]
    for c in chunks:
        try: payload=json.loads(c)
        except Exception: continue
        # payload is a flight string; pull human text from "children":"..." occurrences
        for m in re.finditer(r'"children":("(?:[^"\\]|\\.)*")', payload):
            try: t=json.loads(m.group(1))
            except Exception: continue
            if isinstance(t,str) and len(t)>1: texts.append(t)
        # also plain headings like "### ..." inside paragraph children handled above
    out="\n".join(texts)
    out=re.sub(r'\n{3,}','\n\n',out)
    return out
if __name__=='__main__':
    for url,fn in [(a,b) for a,b in zip(sys.argv[1::2],sys.argv[2::2])]:
        try:
            t=flight_text(url)
            open(fn,'w').write(t)
            print(f"[OK] {fn} {len(t)} chars <- {url}")
        except Exception as e:
            print(f"[FAIL] {url}: {e}")

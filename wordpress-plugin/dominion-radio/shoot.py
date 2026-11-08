import asyncio, json, sys
from playwright.async_api import async_playwright
VPS={"desktop-1440x900":(1440,900),"tablet-768x1024":(768,1024),"mobile-390x844":(390,844),"small-375x812":(375,812)}
async def main(only=None):
    out={}
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for c in ["a","b","c"]:
            for name,(w,h) in VPS.items():
                ctx=await b.new_context(viewport={"width":w,"height":h},device_scale_factor=1,has_touch=(w<800))
                pg=await ctx.new_page()
                await pg.goto(f"http://127.0.0.1:8765/concept-{c}.html",wait_until="load")
                await pg.wait_for_function("document.querySelector('.dr-player__track-title').textContent!=='Select a track'",timeout=8000)
                await pg.add_style_tag(content=".mock-note{display:none!important}"); await pg.wait_for_timeout(700)
                await pg.screenshot(path=f"shots/{c}-{name}-fold.png")
                await pg.screenshot(path=f"shots/{c}-{name}-full.png",full_page=True)
                m=await pg.evaluate("""()=>{const de=document.documentElement;
                  const q=s=>document.querySelector(s);const r=e=>{const b=e.getBoundingClientRect();return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)}};
                  const small=[...document.querySelectorAll('.dr-btn')].filter(e=>e.offsetParent!==null).map(e=>({cls:e.className.replace('dr-btn ',''),...r(e)}));
                  const img=q('.dr-figure img');
                  return {scrollW:de.scrollWidth,clientW:de.clientWidth,docH:de.scrollHeight,
                   playBottom:r(q('.dr-btn--play')).y+r(q('.dr-btn--play')).h, buttons:small, prog:r(q('.dr-player__progress')),
                   img:r(img), imgNat:[img.naturalWidth,img.naturalHeight], status:q('.dr-identity__status-text').textContent, title:q('.dr-player__track-title').textContent,
                   next:q('.dr-player__next').hidden?'hidden':q('.dr-player__next-title').textContent}}""")
                out[f"{c}-{name}"]=m
                await ctx.close()
        await b.close()
    json.dump(out,open("shots/metrics.json","w"),indent=1)
    for k,v in out.items(): print(k,"overflow" if v["scrollW"]>v["clientW"] else "ok","docH",v["docH"],"playBottom",v["playBottom"],"img",v["img"]["w"],"x",v["img"]["h"])
asyncio.run(main())

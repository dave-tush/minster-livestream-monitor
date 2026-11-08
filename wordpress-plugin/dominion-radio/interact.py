import asyncio, json
from playwright.async_api import async_playwright
async def main():
    res={}
    async with async_playwright() as p:
        b=await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        for c in "abc":
            ctx=await b.new_context(viewport={"width":390,"height":844},has_touch=True)
            pg=await ctx.new_page(); errs=[]
            pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto(f"http://127.0.0.1:8765/concept-{c}.html")
            await pg.wait_for_function("document.querySelector('.dr-player__track-title').textContent!=='Select a track'")
            r={}
            play=pg.locator(".dr-btn--play"); mute=pg.locator(".dr-btn--mute")
            await play.click(); await pg.wait_for_timeout(1200)
            r["after_play_pressed"]=await play.get_attribute("aria-pressed"); r["play_label"]=await play.get_attribute("aria-label")
            r["pause_icon_visible"]=await pg.evaluate("!document.querySelector('.dr-icon--pause').hidden")
            r["audio_paused"]=await pg.evaluate("document.querySelector('.dr-audio').paused")
            t1=await pg.evaluate("document.querySelector('.dr-audio').currentTime"); await pg.wait_for_timeout(1500)
            t2=await pg.evaluate("document.querySelector('.dr-audio').currentTime"); r["audio_advancing"]=t2>t1
            await mute.click(); r["muted"]=await pg.evaluate("document.querySelector('.dr-audio').muted"); r["mute_label"]=await mute.get_attribute("aria-label")
            await mute.click()
            box=await pg.locator(".dr-player__progress").bounding_box()
            await pg.mouse.click(box["x"]+box["width"]*0.25,box["y"]+box["height"]/2); await pg.wait_for_timeout(300)
            r["seek_pct_after_click_25"]=round(await pg.evaluate("document.querySelector('.dr-audio').currentTime/document.querySelector('.dr-audio').duration*100"))
            await play.click(); await pg.wait_for_timeout(300); r["paused_again"]=await pg.evaluate("document.querySelector('.dr-audio').paused")
            r["touch_targets_px"]={k:v for k,v in (await pg.evaluate("""()=>Object.fromEntries([...document.querySelectorAll('.dr-btn')].filter(e=>e.offsetParent).map(e=>{const b=e.getBoundingClientRect();return [e.className.split(' ').pop(),Math.round(b.width)+'x'+Math.round(b.height)]}))""")).items()}
            r["progress_hit_area"]=f'{round(box["width"])}x{round(box["height"])}'
            r["js_errors"]=errs
            res[c]=r; await ctx.close()
        # offline/error state
        for c in "abc":
            ctx=await b.new_context(viewport={"width":390,"height":844}); pg=await ctx.new_page()
            await pg.route("**/radio/now",lambda r:r.fulfill(status=503,body="x"))
            await pg.goto(f"http://127.0.0.1:8765/concept-{c}.html"); await pg.wait_for_timeout(1200)
            await pg.add_style_tag(content=".mock-note{display:none!important}")
            await pg.screenshot(path=f"shots/{c}-offline-mobile.png")
            res[c]["offline_visible"]=await pg.evaluate("!document.querySelector('.dr-offline').hidden")
            await ctx.close()
        await b.close()
    print(json.dumps(res,indent=1))
asyncio.run(main())

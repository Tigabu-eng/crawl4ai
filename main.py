from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import httpx
from playwright.async_api import async_playwright
from fastapi import HTTPException
from fastapi.responses import JSONResponse
app = FastAPI()

# Enable CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with your frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cloudinary config
CLOUDINARY_UPLOAD_URL = "https://api.cloudinary.com/v1_1/dwvhna4j2/image/upload"
UPLOAD_PRESET = "unsigned_auto"

async def upload_to_cloudinary(img_bytes: bytes):
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                CLOUDINARY_UPLOAD_URL,
                files={"file": img_bytes},
                data={"upload_preset": UPLOAD_PRESET}
            )
            return response.json().get("secure_url")
    except Exception as e:
        print(f"[Cloudinary Upload Failed] {e}")
        return None

# ----------------------------------------
# 1 Ontario (OpenRoom)
# ----------------------------------------
async def scrape_openroom(name: str):
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://openroom.ca/documents", wait_until="networkidle")
        await page.fill("#search-dropdown", name)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(5000)

        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a.w-full'))
                .filter(a => a.href.includes('/documents/profile'))
                .map(a => a.href.startsWith('http') ? a.href : 'https://openroom.ca' + a.getAttribute('href'))
        """)

        for link in links:
            try:
                case_page = await browser.new_page()
                await case_page.goto(link, wait_until="networkidle")
                await asyncio.sleep(1)

                await case_page.evaluate("""
                    () => {
                        const span = Array.from(document.querySelectorAll('span'))
                            .find(el => el.textContent.includes('View court order'));
                        if (span && span.parentElement) span.parentElement.click();
                    }
                """)
                await asyncio.sleep(3)
                await case_page.evaluate("window.scrollBy(0, 2000)")
                await asyncio.sleep(2)

                metadata = await case_page.evaluate("""
                    () => {
                        const extract = (label) => {
                            const block = Array.from(document.querySelectorAll('div'))
                                .find(el => el.innerText.includes(label));
                            if (!block) return null;
                            const text = block.innerText;
                            return text.split(label)[1]?.trim().split('\\n')[0] || null;
                        };
                        return {
                            tenant: extract('Tenant'),
                            landlord: extract('Landlord'),
                            fileNumber: extract('File Number'),
                            address: extract('Property Address'),
                            topic: extract('Topics'),
                            amountOwed: extract('Amount owed'),
                        };
                    }
                """)

                img_elements = await case_page.query_selector_all("div.mt-2.flex.flex-col.gap-y-2 img")
                cloud_imgs = []

                for i, img in enumerate(img_elements):
                    try:
                        img_url = await img.get_attribute("src")
                        if img_url:
                            response = await case_page.request.get(img_url)
                            if response.ok:
                                img_bytes = await response.body()
                                uploaded = await upload_to_cloudinary(img_bytes)
                                if uploaded:
                                    cloud_imgs.append(uploaded)
                    except Exception as e:
                        print(f"[Image Error] Image {i+1}: {e}")

                results.append({
                    "provider": "OPENROOM",
                    "links": [link],
                    "tenantName": metadata.get("tenant"),
                    "landlord": metadata.get("landlord"),
                    "caseId": metadata.get("fileNumber"),
                    "address": metadata.get("address"),
                    "topic": metadata.get("topic"),
                    "amountOwed": metadata.get("amountOwed"),
                    "courtOrderImages": cloud_imgs
                })
                await case_page.close()
            except Exception as e:
                print(f"[OpenRoom Error] Failed scraping {link}: {e}")
        await browser.close()
    return results

# ----------------------------------------
# 2 Quebec (CanLII)
# ----------------------------------------
async def scrape_quebec(name: str):
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.canlii.org/qc", wait_until="domcontentloaded")

        # Wait for and handle cookie popup
        try:
            if await page.is_visible("#cookieConsentContainer"):
                await page.click("#cookieConsentContainer button.btn")
                await page.wait_for_selector("#cookieConsentContainer", state="hidden", timeout=5000)
                print("[Cookie] Consent accepted.")
        except Exception as e:
            print("[Cookie] Failed to handle popup:", e)

        # Perform search
        await page.wait_for_selector("#idInput")
        await page.fill("#idInput", name)
        await page.keyboard.press("Enter")

        await page.wait_for_selector("li.result", timeout=15000)

        # Try to click on the "Decisions" filter
        try:
            await page.click("#typeFacetItem-decision a", timeout=5000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[Click Fallback] Normal click failed: {e}")
            try:
                await page.evaluate("""
                    () => document.querySelector('#typeFacetItem-decision a')?.click()
                """)
                await asyncio.sleep(2)
                print("[Click Fallback] Clicked using JS.")
            except Exception as e2:
                print("[Click Fallback] JS click also failed:", e2)

        summaries = await page.evaluate("""
            () => Array.from(document.querySelectorAll('li.result')).map(el => {
                const nameAnchor = el.querySelector(".name a");
                const citation = el.querySelector(".reference")?.innerText || null;
                const context = el.querySelectorAll(".context");
                const tribunal = context[0]?.innerText || null;
                const date = context[1]?.innerText || null;
                const keywords = el.querySelector(".keywords")?.innerText || null;
                return {
                    caseName: nameAnchor?.innerText || null,
                    caseUrl: nameAnchor ? "https://www.canlii.org" + nameAnchor.getAttribute("href") : null,
                    citation,
                    tribunal,
                    date,
                    keywords
                };
            })
        """)



        for summary in summaries:
            full_text = None
            if summary["caseUrl"]:
                try:
                    case_page = await browser.new_page()
                    await case_page.goto(summary["caseUrl"], wait_until="domcontentloaded")
                    await case_page.wait_for_selector("#originalDocument", timeout=20000)
                    full_text = await case_page.locator("#originalDocument").inner_text()
                    await case_page.close()
                except Exception as e:
                    print(f"[Quebec Error] Could not load full text: {summary['caseUrl']} → {e}")

            results.append({
                "provider": "CANLII-QUEBEC",
                "caseName": summary["caseName"],
                "citation": summary["citation"],
                "tribunal": summary["tribunal"],
                "date": summary["date"],
                "keywords": summary["keywords"],
                "caseUrl": summary["caseUrl"],
                "fullTextSnippet": full_text
            })
        await browser.close()
    return results

# ----------------------------------------
# 3 Quebec (Alberta)
# ----------------------------------------

async def scrape_alberta(name: str):
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://www.canlii.org/en/ab/", wait_until="domcontentloaded")

        # Handle cookie popup
        try:
            if await page.is_visible("#cookieConsentContainer"):
                await page.click("#cookieConsentContainer button.btn")
                await page.wait_for_selector("#cookieConsentContainer", state="hidden", timeout=5000)
                print("[Cookie] Consent accepted.")
        except Exception as e:
            print("[Cookie] Failed to handle popup:", e)

        # Search
        await page.wait_for_selector("#idInput")
        await page.fill("#idInput", name)
        await page.keyboard.press("Enter")
        await page.wait_for_selector("li.result", timeout=15000)

        # Click "Decisions" filter
        try:
            await page.click("#typeFacetItem-decision a", timeout=5000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[Click Fallback] Normal click failed: {e}")
            try:
                await page.evaluate("() => document.querySelector('#typeFacetItem-decision a')?.click()")
                await asyncio.sleep(2)
            except Exception as e2:
                print("[Click Fallback] JS click also failed:", e2)

        # Loop through all pages
        while True:
            await page.wait_for_selector("li.result")

            summaries = await page.evaluate("""
                () => Array.from(document.querySelectorAll('li.result')).map(el => {
                    const nameAnchor = el.querySelector(".name a");
                    const citation = el.querySelector(".reference")?.innerText || null;
                    const context = el.querySelectorAll(".context");
                    const tribunal = context[0]?.innerText || null;
                    const date = context[1]?.innerText || null;
                    const keywords = el.querySelector(".keywords")?.innerText || null;
                    return {
                        caseName: nameAnchor?.innerText || null,
                        caseUrl: nameAnchor ? "https://www.canlii.org" + nameAnchor.getAttribute("href") : null,
                        citation,
                        tribunal,
                        date,
                        keywords
                    };
                })
            """)

            for summary in summaries:
                full_text = None
                if summary["caseUrl"]:
                    try:
                        case_page = await browser.new_page()
                        await case_page.goto(summary["caseUrl"], wait_until="domcontentloaded")
                        await case_page.wait_for_selector("#originalDocument", timeout=20000)
                        full_text = await case_page.locator("#originalDocument").inner_text()
                        await case_page.close()
                    except Exception as e:
                        print(f"[Alberta Error] Could not load full text: {summary['caseUrl']} → {e}")

                results.append({
                    "provider": "CANLII-ALBERTA",
                    "caseName": summary["caseName"],
                    "citation": summary["citation"],
                    "tribunal": summary["tribunal"],
                    "date": summary["date"],
                    "keywords": summary["keywords"],
                    "caseUrl": summary["caseUrl"],
                    "fullTextSnippet": full_text
                })

            # Pagination
            try:
                next_button = await page.query_selector("a.next")
                if next_button:
                    await next_button.click()
                    await asyncio.sleep(3)
                else:
                    break
            except Exception as e:
                print("[Pagination] No next page or failed:", e)
                break

        await browser.close()

    return results


# ----------------------------------------
# 4 British Columbia (CanLII)
# ----------------------------------------

async def scrape_british_columbia(name: str):
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://www.canlii.org/en/bc/", wait_until="domcontentloaded")

        # Handle cookie popup (optional but safe)
        try:
            if await page.is_visible("#cookieConsentContainer"):
                await page.click("#cookieConsentContainer button.btn")
                await page.wait_for_selector("#cookieConsentContainer", state="hidden", timeout=5000)
        except Exception as e:
            print("[Cookie] Skipped or failed:", e)

        # Search
        await page.wait_for_selector("#idInput")
        await page.fill("#idInput", name)
        await page.keyboard.press("Enter")

        # Wait for search results to load
        try:
            await page.wait_for_selector("li.result", timeout=15000)
        except Exception as e:
            print(f"[BC] No results or timeout: {e}")
            await browser.close()
            return []

        # Try to click "Decisions" filter
        try:
            await page.click("#typeFacetItem-decision a", timeout=5000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[BC] Filter click failed: {e}")
            try:
                await page.evaluate("() => document.querySelector('#typeFacetItem-decision a')?.click()")
                await asyncio.sleep(2)
            except Exception as e2:
                print("[BC] JS click also failed:", e2)

        # Loop through result pages
        while True:
            await page.wait_for_selector("li.result")

            summaries = await page.evaluate("""
                () => Array.from(document.querySelectorAll('li.result')).map(el => {
                    const nameAnchor = el.querySelector(".name a");
                    const citation = el.querySelector(".reference")?.innerText || null;
                    const context = el.querySelectorAll(".context");
                    const tribunal = context[0]?.innerText || null;
                    const date = context[1]?.innerText || null;
                    const keywords = el.querySelector(".keywords")?.innerText || null;
                    return {
                        caseName: nameAnchor?.innerText || null,
                        caseUrl: nameAnchor ? "https://www.canlii.org" + nameAnchor.getAttribute("href") : null,
                        citation,
                        tribunal,
                        date,
                        keywords
                    };
                })
            """)

            for summary in summaries:
                full_text = None
                if summary["caseUrl"]:
                    try:
                        case_page = await browser.new_page()
                        await case_page.goto(summary["caseUrl"], wait_until="domcontentloaded")
                        await case_page.wait_for_selector("#originalDocument", timeout=20000)
                        full_text = await case_page.locator("#originalDocument").inner_text()
                        await case_page.close()
                    except Exception as e:
                        print(f"[BC Error] Could not load full text: {summary['caseUrl']} → {e}")

                results.append({
                    "provider": "CANLII-BC",
                    "caseName": summary["caseName"],
                    "citation": summary["citation"],
                    "tribunal": summary["tribunal"],
                    "date": summary["date"],
                    "keywords": summary["keywords"],
                    "caseUrl": summary["caseUrl"],
                    "fullTextSnippet": full_text
                })

            # Pagination
            try:
                next_button = await page.query_selector("a.next")
                if next_button:
                    await next_button.click()
                    await asyncio.sleep(3)
                else:
                    break
            except Exception as e:
                print("[BC Pagination] No next page or failed:", e)
                break

        await browser.close()

    return results


# ----------------------------------------
# FastAPI Endpoints
# ----------------------------------------
@app.get("/scrape")
async def scrape(name: str = Query(..., description="Search name (OpenRoom - Ontario)")):
    try:
        data = await scrape_openroom(name)
        return {"results": data or []}
    except Exception as e:
        print(f"[ERROR /scrape - Ontario] {e}")
        return JSONResponse(status_code=500, content={
            "error": "Failed to scrape Ontario data",
            "details": str(e)
        })

@app.get("/scrape-quebec")
async def scrape_quebec_endpoint(name: str = Query(..., description="Search name (CanLII - Quebec)")):
    try:
        data = await scrape_quebec(name)
        return {"results": data or []}
    except Exception as e:
        print(f"[ERROR /scrape-quebec] {e}")
        return JSONResponse(status_code=500, content={
            "error": "Failed to scrape Quebec data",
            "details": str(e)
        })

@app.get("/scrape-alberta")
async def scrape_alberta_endpoint(name: str = Query(..., description="Search name (CanLII - Alberta)")):
    try:
        data = await scrape_alberta(name)
        return {"results": data or []}
    except Exception as e:
        print(f"[ERROR /scrape-alberta] {e}")
        return JSONResponse(status_code=500, content={
            "error": "Failed to scrape Alberta data",
            "details": str(e)
        })

@app.get("/scrape-bc")
async def scrape_bc_endpoint(name: str = Query(..., description="Search name (CanLII - British Columbia)")):
    try:
        data = await scrape_british_columbia(name)
        return {"results": data or []}
    except Exception as e:
        print(f"[ERROR /scrape-bc] {e}")
        return JSONResponse(status_code=500, content={
            "error": "Failed to scrape British Columbia data",
            "details": str(e)
        })
    

@app.get("/")
async def root():
    return {
        "message": "Welcome to crawl4ai API. Use /scrape, /scrape-all, or a province-specific route to get started.",
        "endpoints": [
            "/scrape?name=",
            "/scrape-all?name=&province=",
            "/scrape-quebec?name=",
            "/scrape-alberta?name=",
            "/scrape-bc?name="
        ]
    }


@app.get("/scrape-all")
async def scrape_all(name: str, province: str = Query("ontario", enum=["ontario", "quebec", "alberta", "bc"])):
    try:
        if province.lower() == "quebec":
            data = await scrape_quebec(name)
        elif province.lower() == "alberta":
            data = await scrape_alberta(name)
        elif province.lower() == "bc":
            data = await scrape_british_columbia(name)
        else:
            data = await scrape_openroom(name)
        return {"results": data or []}
        
    except Exception as e:
        print(f"[ERROR /scrape-all for province={province}] {e}")
        return JSONResponse(status_code=500, content={
            "error": f"Failed to scrape data for {province.title()}",
            "details": str(e)
        })
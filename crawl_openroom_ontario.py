import asyncio
from crawl4ai.async_webcrawler import AsyncWebCrawler
from playwright.async_api import async_playwright
import base64
import requests

# Helper function to convert image URLs to base64
def image_to_base64(url):
    try:
        response = requests.get(url)
        return f"data:image/png;base64,{base64.b64encode(response.content).decode()}"
    except:
        return None

async def scrape_openroom(name):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto("https://openroom.ca/documents", wait_until="networkidle")

        # Search the name
        await page.wait_for_selector("#search-dropdown")
        await page.fill("#search-dropdown", name)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(5000)

        # Collect result links
        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a.w-full'))
                .filter(a => a.href.includes('/documents/profile'))
                .map(a => a.href.startsWith('http') ? a.href : 'https://openroom.ca' + a.getAttribute('href'))
        """)

        results = []

        for link in links:
            case_page = await browser.new_page()
            await case_page.goto(link, wait_until="networkidle")
            await case_page.wait_for_selector("h3")
            await asyncio.sleep(1)

            # Click “View court order”
            await case_page.evaluate("""
                () => {
                    const span = Array.from(document.querySelectorAll('span'))
                        .find(el => el.textContent.includes('View court order'));
                    if (span && span.parentElement) span.parentElement.click();
                }
            """)
            await asyncio.sleep(3)

            # Extract metadata
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

            # Extract and convert image URLs
            image_urls = await case_page.evaluate("""
                () => Array.from(document.querySelectorAll('div.mt-2.flex.flex-col.gap-y-2 img'))
                    .map(img => img.src)
            """)

            images_base64 = [image_to_base64(url) for url in image_urls if url]

            result = {
                "provider": "OPENROOM",
                "sourceUrl": link,
                "tenantName": metadata["tenant"],
                "landlord": metadata["landlord"],
                "caseId": metadata["fileNumber"],
                "address": metadata["address"],
                "topic": metadata["topic"],
                "amountOwed": metadata["amountOwed"],
                "courtOrderImages": images_base64,
            }

            results.append(result)
            await case_page.close()

        await browser.close()
        return results


# Main runner
async def main():
    name = "John Doe"  # Replace this with dynamic input later
    data = await scrape_openroom(name)
    print(data)

if __name__ == "__main__":
    asyncio.run(main())

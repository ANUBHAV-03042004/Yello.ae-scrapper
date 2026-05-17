import time
import re
import sys
import argparse
import os
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import pandas as pd

load_dotenv()

YELLO_EMAIL    = os.getenv("YELLO_EMAIL", "")
YELLO_PASSWORD = os.getenv("YELLO_PASSWORD", "")
DELAY          = 2
EMAIL_DELAY    = 1.5

IS_CI = os.getenv("CI") == "true"  # GitHub Actions sets this automatically

# ────────────────────────────────────────────────────────


def create_driver():
    if IS_CI:
        # ── GitHub Actions: plain Selenium + webdriver-manager ──
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-popup-blocking")

        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)
    else:
        # ── Local Windows: undetected_chromedriver to bypass bot detection ──
        import undetected_chromedriver as uc

        options = uc.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-popup-blocking")
        return uc.Chrome(options=options, version_main=147)


def login(driver, wait):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    print("\n[Login] Navigating to Yello.ae...")
    driver.get("https://www.yello.ae/sign-in")
    time.sleep(4)

    try:
        email_field = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[name='data[Login][username]']")))
        email_field.clear()
        email_field.send_keys(YELLO_EMAIL)
        time.sleep(1)

        pass_field = driver.find_element(
            By.CSS_SELECTOR, "input[name='data[Login][password]']")
        pass_field.clear()
        pass_field.send_keys(YELLO_PASSWORD)
        time.sleep(1)

        login_btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit']")
        login_btn.click()
        time.sleep(4)

        if "sign-in" not in driver.current_url:
            print("[Login] ✅ Login successful!")
            return True
        else:
            soup  = BeautifulSoup(driver.page_source, "html.parser")
            error = soup.select_one("div.error, p.error, span.error")
            print(f"[Login] ❌ Failed: {error.get_text() if error else 'Check credentials in .env'}")
            return False

    except Exception as e:
        print(f"[Login] ❌ Error: {e}")
        return False


def build_url(keyword, location, page=1):
    base = f"https://www.yello.ae/uae-business-search/what:{keyword}/where:{location}"
    return base if page == 1 else f"https://www.yello.ae/uae-business-search/{page}"


def get_total_pages(driver, keyword, location):
    url = build_url(keyword, location, page=1)
    driver.get(url)
    time.sleep(3)

    soup = BeautifulSoup(driver.page_source, "html.parser")

    try:
        text  = soup.get_text()
        match = re.search(r"We found ([\d,]+) compan", text)
        if match:
            total = int(match.group(1).replace(",", ""))
            pages = (total // 20) + 1
            print(f"  Total companies found : {total}")
            print(f"  Total pages           : {pages}")
            return pages, soup
    except Exception as e:
        print(f"  Could not detect total pages: {e}")

    print("  Could not detect total, will stop when no new results found.")
    return 99, soup


def extract_company_id(profile_url):
    match = re.search(r'/company/(\d+)/', profile_url)
    return match.group(1) if match else None


def get_email(driver, company_id):
    try:
        driver.get(f"https://www.yello.ae/getlogin/email:{company_id}")
        time.sleep(EMAIL_DELAY)

        soup      = BeautifulSoup(driver.page_source, "html.parser")
        email_tag = soup.select_one("div.login_message h3")

        if email_tag:
            email = email_tag.get_text(strip=True)
            if "@" in email and "." in email:
                return email

        match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', soup.get_text())
        if match:
            return match.group(0)

    except Exception as e:
        print(f"  [email error] {e}")

    return ""


def parse_businesses(soup):
    results = []
    cards   = soup.select("div.company")
    print(f"  Found {len(cards)} business cards")

    for card in cards:
        try:
            name_tag = card.select_one("h3 a")
            name     = name_tag.get_text(strip=True) if name_tag else ""
            profile  = "https://www.yello.ae" + name_tag["href"] if name_tag else ""

            addr_tag = card.select_one("div.address")
            address  = addr_tag.get_text(separator=" ", strip=True).replace("Address:", "").strip() if addr_tag else ""

            phone = ""
            for s_div in card.select("div.s"):
                icon = s_div.select_one("i")
                span = s_div.select_one("span")
                if not icon or not span:
                    continue
                if icon.get("aria-label", "").lower() == "phone number":
                    phone = span.get_text(strip=True)
                    break

            if name:
                results.append({
                    "Company Name" : name,
                    "Address"      : address,
                    "Phone"        : phone,
                    "Email"        : "",
                    "Profile URL"  : profile,
                })

        except Exception as e:
            print(f"  Error parsing card: {e}")
            continue

    return results


def clean_excel_string(val):
    if not isinstance(val, str):
        return val
    # Remove control characters that Excel doesn't allow (ASCII 0-31 except tab 9, LF 10, CR 13)
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', val)


def clean_records(records):
    cleaned = []
    for r in records:
        cleaned_record = {}
        for k, v in r.items():
            cleaned_record[k] = clean_excel_string(v)
        cleaned.append(cleaned_record)
    return cleaned


def scrape():
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By

    print("=" * 50)
    print("  Yello.ae Business Scraper (with emails)")
    print("=" * 50)

    parser = argparse.ArgumentParser(description="Yello.ae Business Scraper")
    parser.add_argument("--keyword",  type=str, help="Search keyword e.g. restaurant")
    parser.add_argument("--location", type=str, help="Location e.g. Dubai")
    args = parser.parse_args()

    if args.keyword and args.location:
        KEYWORD  = args.keyword.strip()
        LOCATION = args.location.strip()
        print(f"\nKeyword  : {KEYWORD}")
        print(f"Location : {LOCATION}")
    else:
        print()
        KEYWORD  = input("Enter keyword  (e.g. restaurant, hotel, pharmacy): ").strip()
        LOCATION = input("Enter location (e.g. Dubai, Abu Dhabi, Sharjah)  : ").strip()

    print(f"\n[Mode] {'GitHub Actions (CI)' if IS_CI else 'Local Windows'}")

    driver = create_driver()
    wait   = WebDriverWait(driver, 20)

    if not login(driver, wait):
        print("\n❌ Cannot proceed without login. Check your .env / secrets.")
        try:
            driver.quit()
        except Exception:
            pass
        sys.exit(1)

    all_data  = []
    seen_urls = set()

    try:
        print(f"\n[Detecting] Fetching page 1 to count results...")
        MAX_PAGES, first_soup = get_total_pages(driver, KEYWORD, LOCATION)

        if first_soup:
            records = parse_businesses(first_soup)
            for r in records:
                if r["Profile URL"] not in seen_urls:
                    seen_urls.add(r["Profile URL"])
                    all_data.append(r)
            print(f"  Collected so far     : {len(all_data)}")
            start_page = 2
        else:
            start_page = 1

        for page in range(start_page, MAX_PAGES + 1):
            print(f"\n[Page {page}/{MAX_PAGES}] {build_url(KEYWORD, LOCATION, page)}")
            driver.get(f"https://www.yello.ae/uae-business-search/{page}")
            time.sleep(DELAY)

            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.company")))
            except Exception:
                print(f"  No results on page {page}. Stopping.")
                break

            soup    = BeautifulSoup(driver.page_source, "html.parser")
            records = parse_businesses(soup)

            if not records:
                print(f"  No businesses parsed. Stopping.")
                break

            new_records = [r for r in records if r["Profile URL"] not in seen_urls]
            for r in new_records:
                seen_urls.add(r["Profile URL"])

            all_data.extend(new_records)
            print(f"  New unique this page : {len(new_records)}")
            print(f"  Total collected      : {len(all_data)}")

            if not new_records:
                print("  No new unique records. Stopping.")
                break

            if page < MAX_PAGES:
                time.sleep(DELAY)

        print(f"\n{'='*50}")
        print(f"  Fetching emails for {len(all_data)} companies...")
        print(f"{'='*50}")

        found     = 0
        not_found = 0

        for i, record in enumerate(all_data):
            company_id = extract_company_id(record["Profile URL"])

            if not company_id:
                print(f"[{i+1}/{len(all_data)}] ⚠️  No ID — {record['Company Name'][:45]}")
                not_found += 1
                continue

            email = get_email(driver, company_id)
            record["Email"] = email

            if email:
                found += 1
                print(f"[{i+1}/{len(all_data)}] ✅ {record['Company Name'][:40]} → {email}")
            else:
                not_found += 1
                print(f"[{i+1}/{len(all_data)}] ❌ {record['Company Name'][:40]} → no email")

            if (i + 1) % 50 == 0:
                OUTPUT_TEMP = f"yello_{KEYWORD}_{LOCATION}.xlsx".replace(" ", "_").lower()
                pd.DataFrame(clean_records(all_data)).to_excel(OUTPUT_TEMP, index=False)
                print(f"\n  💾 Progress saved — {found} emails so far\n")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if all_data:
        OUTPUT = f"yello_{KEYWORD}_{LOCATION}.xlsx".replace(" ", "_").lower()
        df     = pd.DataFrame(clean_records(all_data))[["Company Name", "Address", "Phone", "Email", "Profile URL"]]
        df.to_excel(OUTPUT, index=False)

        print(f"\n{'='*50}")
        print(f"✅ Done!")
        print(f"   Companies scraped : {len(df)}")
        print(f"   Emails found      : {found}")
        print(f"   No email          : {not_found}")
        print(f"   Success rate      : {found/len(df)*100:.1f}%")
        print(f"   Saved to          : {OUTPUT}")
        print(f"{'='*50}")
        print(f"OUTPUT_FILE={OUTPUT}")
    else:
        print("\n❌ No data collected.")
        sys.exit(1)


if __name__ == "__main__":
    scrape()
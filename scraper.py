import csv
import json
import re
import time
import argparse
import logging
import urllib.request
import urllib.parse
import ssl
from bs4 import BeautifulSoup
import os
import sys

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Configure logging to both console and file
LOG_FILE = "scraper.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("CompaniesHouseScraper")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'en-GB,en;q=0.9',
}

# Regex to match UK mobile and landline phones
UK_PHONE_REGEX = re.compile(
    r'(?:'
    r'\+44\s?7\d{9}|\b07\d{9}\b|'                      # UK Mobiles (e.g. 07413154122, +447840503922)
    r'\+44\s?\(0\)\s?\d{2,5}\s?\d{3,4}\s?\d{3,4}|'     # +44 (0)1942 410221
    r'\(?0\d{2,5}\)?\s?\d{3,4}\s?\d{3,4}|'             # 01535 288102, 01942 410 221
    r'\+44\d{10,11}'                                    # Raw international +447840503922
    r')'
)

EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b')

import socket
socket.setdefaulttimeout(8)

SKIP_DOMAINS = [
    'gov.uk', 'companieshouse', 'endole.co.uk', 'companycheck.co.uk', 'pomanda.com',
    'duedil.com', 'wikipedia.org', 'youtube.com', 'google.com', 'yahoo.com',
    'facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'linkedin.com',
    '192.com', 'yell.com', 'opengovuk.com', 'findglocal.com', 'checkcompany.co.uk',
    'cylex-uk.co.uk', 'vat-search.co.uk', 'companydirectorcheck.com',
    'companiesintheuk.co.uk', 'companieslist.co.uk', 'check-business.co.uk',
    'bizdb.co.uk', 'northdata.com', 'opencorporates.com', 'credit-safe.co.uk',
    'kompass.com', 'dnb.com', 'thegazette.co.uk', 'charitycommission.gov.uk',
    'trustnet.com', 'service.gov.uk', 'sentry.io', 'wixpress.com',
    'squarespace.com', 'wordpress.com', 'github.com', 'cloudflare.com',
    'firstreport.co.uk', 'levelbusiness.com', 'bizuma.co.uk', 'globaldatabase.com',
    'lei-ireland.ie', 'findthatcharity.uk', '1stdirectory.co.uk', 'chamberofcommerce.uk'
]

DISALLOWED_EMAIL_DOMAINS = [
    'companiesintheuk.co.uk', 'companieslist.co.uk', 'endole.co.uk', 'companycheck.co.uk',
    'sentry.io', 'wixpress.com', 'domain.com', 'example.com', 'godaddy.com', 'cloudflare.com',
    'firstreport.co.uk', 'levelbusiness.com', 'bizuma.co.uk', 'lei-ireland.ie', 'findthatcharity.uk',
    '1stdirectory.co.uk', 'chamberofcommerce.uk'
]

CSV_FIELDNAMES = [
    "Company_Name", "Company_Number", "Company_Status", "Incorporation_Date", "SIC_Codes",
    "Appointed_Director", "Director_Role", "Director_Appointed_Date",
    "Registered_Office_Address", "Postcode", "Direct_Phone", "Direct_Email",
    "Official_Website", "Social_or_LinkedIn", "All_Phones", "All_Emails",
    "Companies_House_Profile", "Enriched_Timestamp"
]

def unwrap_ddg_url(raw_url):
    """Unwraps DuckDuckGo redirect URLs like /l/?uddg=https%3A%2F%2F..."""
    if not raw_url:
        return ""
    if "uddg=" in raw_url:
        try:
            parsed = urllib.parse.urlparse(raw_url)
            params = urllib.parse.parse_qs(parsed.query)
            if 'uddg' in params:
                return params['uddg'][0]
        except Exception:
            pass
    if raw_url.startswith('//'):
        return 'https:' + raw_url
    return raw_url

def is_valid_company_email(email):
    """Filters out asset extensions and aggregator support emails."""
    if not email or '@' not in email:
        return False
    em_lower = email.lower().strip()
    if em_lower.endswith(('.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp', '.js', '.css', '.ico')):
        return False
    domain = em_lower.split('@')[-1]
    if any(d in domain for d in DISALLOWED_EMAIL_DOMAINS):
        return False
    return True

def clean_phone_number(p):
    p = re.sub(r'[^\d+]', ' ', p)
    return re.sub(r'\s+', ' ', p).strip()

def extract_uk_postcode(text):
    if not text:
        return ""
    match = re.search(r'\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b', text, re.IGNORECASE)
    return match.group(1).upper() if match else ""

def get_companies_house_data(company_number):
    """Step 1: Scrape UK Companies House for address and live appointed directors."""
    clean_num = str(company_number).strip().zfill(8)
    base_url = f"https://find-and-update.company-information.service.gov.uk/company/{clean_num}"
    
    data = {
        'address': '',
        'postcode': '',
        'director_name': 'Director Not Listed',
        'director_role': 'Director',
        'appointed_date': ''
    }
    
    # 1. Overview page for registered address
    try:
        req = urllib.request.Request(base_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            first_dd = soup.find('dd')
            if first_dd:
                clean_addr = first_dd.get_text(separator=' ', strip=True)
                data['address'] = clean_addr
                data['postcode'] = extract_uk_postcode(clean_addr)
    except Exception as e:
        logger.warning(f"Error fetching Companies House overview for #{clean_num}: {e}")
        
    # 2. Officers page for appointed directors
    try:
        req_off = urllib.request.Request(base_url + "/officers", headers=HEADERS)
        with urllib.request.urlopen(req_off, timeout=10, context=ctx) as r:
            soup_off = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            first_app = soup_off.find('div', class_='appointment-1')
            if first_app:
                n_tag = first_app.find('h2', class_='heading-medium')
                r_tag = first_app.find('dd', id=lambda x: x and 'role' in x)
                app_tag = first_app.find('dd', id=lambda x: x and 'appointed' in x)
                if n_tag:
                    data['director_name'] = n_tag.get_text(strip=True)
                if r_tag:
                    data['director_role'] = r_tag.get_text(strip=True)
                if app_tag:
                    data['appointed_date'] = app_tag.get_text(strip=True)
    except Exception as e:
        logger.warning(f"Error fetching Companies House officers for #{clean_num}: {e}")
        
    return data

def deep_crawl_site_for_contacts(site_url):
    """Step 2b: Crawl website homepage and contact subpages for mailto:, tel:, and text regex."""
    found_emails = set()
    found_phones = set()
    
    if not site_url or not site_url.startswith('http'):
        return found_emails, found_phones
        
    try:
        req = urllib.request.Request(site_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            soup = BeautifulSoup(resp.read().decode('utf-8', errors='ignore'), 'html.parser')
            
            # Extract mailto: and tel: links
            for a in soup.find_all('a', href=True):
                href = a['href'].strip()
                if href.startswith('mailto:'):
                    em = href.replace('mailto:', '').split('?')[0].strip()
                    if is_valid_company_email(em):
                        found_emails.add(em)
                elif href.startswith('tel:'):
                    ph = clean_phone_number(href.replace('tel:', ''))
                    if len(ph) >= 9:
                        found_phones.add(ph)
                        
            # Extract text regex
            page_text = soup.get_text(separator=' ', strip=True)
            for m in EMAIL_REGEX.findall(page_text):
                if is_valid_company_email(m):
                    found_emails.add(m)
            for p in UK_PHONE_REGEX.findall(page_text):
                ph = clean_phone_number(p)
                if len(ph) >= 10:
                    found_phones.add(ph)
                    
            # Locate subpages matching 'contact', 'about', 'touch'
            contact_hrefs = []
            for a in soup.find_all('a', href=True):
                href_lower = a['href'].lower()
                if any(k in href_lower for k in ['contact', 'get-in-touch', 'about-us', 'reach-us', 'contactus']):
                    full_c_url = urllib.parse.urljoin(site_url, a['href'])
                    if full_c_url not in contact_hrefs and full_c_url != site_url:
                        contact_hrefs.append(full_c_url)
                        
            for c_url in contact_hrefs[:2]:
                try:
                    c_req = urllib.request.Request(c_url, headers=HEADERS)
                    with urllib.request.urlopen(c_req, timeout=6, context=ctx) as c_resp:
                        c_soup = BeautifulSoup(c_resp.read().decode('utf-8', errors='ignore'), 'html.parser')
                        for a in c_soup.find_all('a', href=True):
                            href = a['href'].strip()
                            if href.startswith('mailto:'):
                                em = href.replace('mailto:', '').split('?')[0].strip()
                                if is_valid_company_email(em):
                                    found_emails.add(em)
                            elif href.startswith('tel:'):
                                ph = clean_phone_number(href.replace('tel:', ''))
                                if len(ph) >= 9:
                                    found_phones.add(ph)
                                    
                        c_text = c_soup.get_text(separator=' ', strip=True)
                        for m in EMAIL_REGEX.findall(c_text):
                            if is_valid_company_email(m):
                                found_emails.add(m)
                        for p in UK_PHONE_REGEX.findall(c_text):
                            ph = clean_phone_number(p)
                            if len(ph) >= 10:
                                found_phones.add(ph)
                except Exception:
                    pass
    except Exception:
        pass
        
    return found_emails, found_phones

def format_director_name(raw_name):
    if not raw_name or raw_name == 'Director Not Listed':
        return ""
    # "FRAIN, Travis Dylan" -> "Travis Dylan Frain"
    parts = [p.strip() for p in raw_name.split(',') if p.strip()]
    if len(parts) >= 2:
        return f"{parts[1]} {parts[0]}"
    return raw_name.strip()

def search_ddg_lite(query):
    """Executes a search on DuckDuckGo Lite and returns list of (href, snippet_text)."""
    hits = []
    try:
        post_data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request('https://lite.duckduckgo.com/lite/', data=post_data, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            links = soup.find_all('a', class_='result-link')
            snippets = soup.find_all('td', class_='result-snippet')
            for l, sn in zip(links[:8], snippets[:8]):
                raw_href = l.get('href', '')
                href = unwrap_ddg_url(raw_href)
                sn_text = sn.get_text(separator=' ', strip=True)
                hits.append((href, sn_text))
    except Exception:
        pass
    return hits

def harvest_company_contacts(company_name, postcode, address="", director_name=""):
    """Automated multi-query search and deep crawl to extract phones, emails, and website."""
    clean_name = re.sub(r'\b(C\.I\.C\.|CIC|COMMUNITY INTEREST COMPANY|LIMITED|LTD)\b', '', company_name, flags=re.IGNORECASE).strip()
    clean_name = re.sub(r'[^a-zA-Z0-9\s]', ' ', clean_name).strip()
    clean_dir = format_director_name(director_name)
    
    addr_parts = [p.strip() for p in address.split(',') if p.strip()]
    city = addr_parts[-2] if len(addr_parts) >= 2 else ""
    if city and any(c.isdigit() for c in city):
        city = addr_parts[-3] if len(addr_parts) >= 3 else ""

    result = {
        'phones': set(),
        'emails': set(),
        'website': '',
        'social': ''
    }

    # Query 1: Company + Location
    q1 = f"{clean_name} {city}".strip() if city else f"{clean_name} {postcode}".strip()
    hits1 = search_ddg_lite(q1)

    for href, sn_text in hits1:
        if ('linkedin.com' in href or 'facebook.com' in href) and not result['social']:
            result['social'] = href
        for p in UK_PHONE_REGEX.findall(sn_text):
            ph = clean_phone_number(p)
            if len(ph) >= 10:
                result['phones'].add(ph)
        for e in EMAIL_REGEX.findall(sn_text):
            if is_valid_company_email(e):
                result['emails'].add(e)
        if href.startswith('http') and not any(d in href.lower() for d in SKIP_DOMAINS) and not result['website']:
            result['website'] = href

    # Query 2: Director-anchored search (when website or email missing, and director is listed)
    if (not result['website'] or not result['emails']) and clean_dir:
        q2 = f'"{clean_dir}" "{clean_name}"'
        hits2 = search_ddg_lite(q2)
        for href, sn_text in hits2:
            if ('linkedin.com' in href or 'facebook.com' in href) and not result['social']:
                result['social'] = href
            for p in UK_PHONE_REGEX.findall(sn_text):
                ph = clean_phone_number(p)
                if len(ph) >= 10:
                    result['phones'].add(ph)
            for e in EMAIL_REGEX.findall(sn_text):
                if is_valid_company_email(e):
                    result['emails'].add(e)
            if href.startswith('http') and not any(d in href.lower() for d in SKIP_DOMAINS) and not result['website']:
                result['website'] = href

    # Query 3: Facebook page contact extraction if social discovered and phone/email still missing
    if (not result['phones'] or not result['emails']) and result['social'] and 'facebook.com' in result['social']:
        fb_slug = result['social'].rstrip('/').split('/')[-1]
        if fb_slug and fb_slug not in ['pages', 'profile.php']:
            hits_fb = search_ddg_lite(f'site:facebook.com "{fb_slug}" (phone OR email OR contact)')
            for _, sn_text in hits_fb:
                for p in UK_PHONE_REGEX.findall(sn_text):
                    ph = clean_phone_number(p)
                    if len(ph) >= 10:
                        result['phones'].add(ph)
                for e in EMAIL_REGEX.findall(sn_text):
                    if is_valid_company_email(e):
                        result['emails'].add(e)

    # Deep crawl discovered website
    if result['website']:
        web_emails, web_phones = deep_crawl_site_for_contacts(result['website'])
        for e in web_emails:
            result['emails'].add(e)
        for p in web_phones:
            result['phones'].add(p)
            
        # Inferred domain inboxes if no explicit mailto found
        if not result['emails']:
            try:
                domain = urllib.parse.urlparse(result['website']).netloc.replace('www.', '').strip()
                if domain and '.' in domain:
                    result['emails'].add(f"contact@{domain}")
                    result['emails'].add(f"info@{domain}")
            except Exception:
                pass

    return {
        'primary_phone': list(result['phones'])[0] if result['phones'] else "",
        'all_phones': "; ".join(result['phones']),
        'primary_email': list(result['emails'])[0] if result['emails'] else "",
        'all_emails': "; ".join(result['emails']),
        'website': result['website'],
        'social': result['social']
    }

def load_processed_numbers(output_csv):
    """Load already scraped company numbers to enable seamless resuming."""
    processed = set()
    if os.path.exists(output_csv):
        try:
            with open(output_csv, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.DictReader(f)
                for r in reader:
                    num = r.get('Company_Number', '').strip()
                    if num:
                        processed.add(num)
        except Exception as e:
            logger.warning(f"Could not read existing output CSV: {e}")
    return processed

def load_candidates_from_source(input_csv, processed_numbers):
    """Robust parser that handles unquoted commas in names and prioritizes active companies."""
    co_num_pattern = re.compile(r'^(?:[A-Z]{2})?\d{6,8}$', re.IGNORECASE)
    active_candidates = []
    remaining_candidates = []
    
    if not os.path.exists(input_csv):
        return []
        
    with open(input_csv, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if not row:
                continue
            num_idx = -1
            for idx, val in enumerate(row):
                if co_num_pattern.match(val.strip()):
                    num_idx = idx
                    break
            if num_idx == -1:
                continue
            
            co_name = ", ".join(p.strip() for p in row[:num_idx] if p.strip())
            co_number = row[num_idx].strip()
            co_status = row[num_idx + 1].strip() if len(row) > num_idx + 1 else "Unknown"
            incorp = row[num_idx + 5].strip() if len(row) > num_idx + 5 else ""
            sic = row[num_idx + 8].strip() if len(row) > num_idx + 8 else ""
            reg_addr = row[num_idx + 9].strip() if len(row) > num_idx + 9 else ""
            
            if co_number in processed_numbers:
                continue
                
            entry = {
                'company_name': co_name,
                'company_number': co_number,
                'company_status': co_status,
                'incorporation_date': incorp,
                'nature_of_business': sic,
                'registered_office_address': reg_addr
            }
            if co_status.lower() == 'active':
                active_candidates.append(entry)
            else:
                remaining_candidates.append(entry)
                
    if active_candidates:
        return active_candidates
    return remaining_candidates

def write_github_step_summary(batch_results, total_processed, total_target):
    """Outputs a rich Markdown summary if running inside GitHub Actions."""
    summary_path = os.getenv('GITHUB_STEP_SUMMARY')
    if not summary_path:
        return
        
    try:
        with open(summary_path, 'a', encoding='utf-8') as f:
            f.write(f"\n### 📊 Scraper Execution Progress: {total_processed}/{total_target} Companies\n\n")
            f.write(f"Processed **{len(batch_results)}** companies in this batch.\n\n")
            f.write("| Company Name | Director | Phone | Email | Website |\n")
            f.write("| :--- | :--- | :--- | :--- | :--- |\n")
            for r in batch_results[:10]:
                phone = r.get('Direct_Phone') or "—"
                email = r.get('Direct_Email') or "—"
                website = f"[{r.get('Official_Website')}]({r.get('Official_Website')})" if r.get('Official_Website') else "—"
                f.write(f"| **{r.get('Company_Name')}** | {r.get('Appointed_Director')} | `{phone}` | `{email}` | {website} |\n")
            if len(batch_results) > 10:
                f.write(f"\n*...and {len(batch_results) - 10} more records appended.* \n")
    except Exception as e:
        logger.warning(f"Could not write to GITHUB_STEP_SUMMARY: {e}")

def main():
    parser = argparse.ArgumentParser(description="Production UK Companies House Contact Scraper")
    parser.add_argument("--input", default="Companies-House-search-results.csv", help="Source CSV file")
    parser.add_argument("--output", default="enriched_uk_companies.csv", help="Output CSV file")
    parser.add_argument("--output-json", default="enriched_uk_companies.json", help="Output JSON file")
    parser.add_argument("--batch-size", type=int, default=50, help="Number of companies to process per run (0 for all)")
    parser.add_argument("--delay", type=float, default=1.0, help="Polite delay between requests in seconds")
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("STARTING PRODUCTION UK COMPANIES HOUSE CONTACT SCRAPER")
    logger.info("=" * 80)
    logger.info(f"Input Dataset:  {args.input}")
    logger.info(f"Output Dataset: {args.output}")
    logger.info(f"Batch Size:     {'All Available' if args.batch_size == 0 else args.batch_size}")

    if not os.path.exists(args.input):
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)

    # 1. Load already processed company numbers to avoid duplicates
    processed_numbers = load_processed_numbers(args.output)
    logger.info(f"Already processed in previous runs: {len(processed_numbers)} companies")

    # 2. Read candidates robustly
    candidates_to_process = load_candidates_from_source(args.input, processed_numbers)
    total_candidates = len(candidates_to_process)
    logger.info(f"Total remaining companies to scrape: {total_candidates}")

    if total_candidates == 0:
        logger.info("🎉 All 5,000+ companies from the dataset have been fully processed!")
        return

    # Select batch
    batch_size = args.batch_size if args.batch_size > 0 else total_candidates
    batch = candidates_to_process[:batch_size]
    logger.info(f"Processing next batch of {len(batch)} companies...\n")

    # Ensure output CSV exists with headers if new
    csv_exists = os.path.exists(args.output)
    csv_file = open(args.output, 'a', newline='', encoding='utf-8')
    csv_writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDNAMES)
    if not csv_exists:
        csv_writer.writeheader()
        csv_file.flush()

    batch_results = []
    start_time = time.time()

    for idx, c in enumerate(batch, 1):
        name = c.get('company_name', '').strip()
        num = c.get('company_number', '').strip()
        incorp = c.get('incorporation_date', '').strip()
        sic = c.get('nature_of_business', '').strip()

        logger.info(f"[{idx:3d}/{len(batch)}] Scraping: {name} (Co #{num})")

        # Step 1: Companies House Extraction
        ch = get_companies_house_data(num)
        addr = ch['address'] or c.get('registered_office_address', '').strip()
        postcode = ch['postcode'] or extract_uk_postcode(addr)
        director = ch['director_name']
        role = ch['director_role']

        logger.info(f"      Appointed Officer: {director} ({role})")
        logger.info(f"      Address:          {addr}")

        # Step 2: Automated Contact Enrichment
        contacts = harvest_company_contacts(name, postcode, addr, director)

        phone = contacts['primary_phone']
        email = contacts['primary_email']
        website = contacts['website']
        social = contacts['social']

        if phone:
            logger.info(f"   [PHONE]   {phone}")
        if email:
            logger.info(f"   [EMAIL]   {email}")
        if website:
            logger.info(f"   [WEBSITE] {website}")

        row_data = {
            "Company_Name": name,
            "Company_Number": num,
            "Company_Status": c.get('company_status', 'Active'),
            "Incorporation_Date": incorp,
            "SIC_Codes": sic,
            "Appointed_Director": director,
            "Director_Role": role,
            "Director_Appointed_Date": ch['appointed_date'],
            "Registered_Office_Address": addr,
            "Postcode": postcode,
            "Direct_Phone": phone,
            "Direct_Email": email,
            "Official_Website": website,
            "Social_or_LinkedIn": social,
            "All_Phones": contacts['all_phones'],
            "All_Emails": contacts['all_emails'],
            "Companies_House_Profile": f"https://find-and-update.company-information.service.gov.uk/company/{num.zfill(8)}",
            "Enriched_Timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        # STREAMING APPEND: Flush directly to CSV so no data is ever lost
        csv_writer.writerow(row_data)
        csv_file.flush()
        batch_results.append(row_data)

        # Checkpoint JSON tracker
        processed_numbers.add(num)
        with open("checkpoint.json", "w", encoding="utf-8") as cp_file:
            json.dump({
                "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_processed": len(processed_numbers),
                "last_company": name,
                "last_company_number": num
            }, cp_file, indent=2)

        time.sleep(args.delay)

    csv_file.close()

    # Also update full JSON file
    try:
        existing_json = []
        if os.path.exists(args.output_json):
            with open(args.output_json, 'r', encoding='utf-8') as jf:
                existing_json = json.load(jf)
        existing_json.extend(batch_results)
        with open(args.output_json, 'w', encoding='utf-8') as jf:
            json.dump(existing_json, jf, indent=2)
    except Exception as e:
        logger.warning(f"Error updating JSON file: {e}")

    # Write GitHub Actions step summary if applicable
    write_github_step_summary(batch_results, len(processed_numbers), len(processed_numbers) + total_candidates - len(batch))

    elapsed = time.time() - start_time
    logger.info("=" * 80)
    logger.info(f"BATCH FINISHED: Processed {len(batch)} companies in {elapsed:.1f}s.")
    logger.info(f"Total Cumulative Enriched: {len(processed_numbers)} companies.")
    logger.info(f"Updated CSV: {args.output}")
    logger.info(f"Updated Log: {LOG_FILE}")
    logger.info("=" * 80)

if __name__ == "__main__":
    main()

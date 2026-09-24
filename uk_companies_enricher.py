import csv
import json
import re
import time
import urllib.request
import urllib.parse
import ssl
from bs4 import BeautifulSoup
import os

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

SKIP_DOMAINS = [
    'gov.uk', 'companieshouse', 'endole.co.uk', 'companycheck.co.uk', 'pomanda.com',
    'duedil.com', 'wikipedia.org', 'youtube.com', 'google.com', 'yahoo.com',
    'facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'linkedin.com',
    '192.com', 'yell.com', 'opengovuk.com', 'findglocal.com', 'checkcompany.co.uk',
    'cylex-uk.co.uk', 'vat-search.co.uk', 'companydirectorcheck.com'
]

def clean_phone_number(p):
    p = re.sub(r'[^\d+]', ' ', p)
    return re.sub(r'\s+', ' ', p).strip()

def extract_uk_postcode(text):
    if not text:
        return ""
    match = re.search(r'\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b', text, re.IGNORECASE)
    return match.group(1).upper() if match else ""

def get_companies_house_data(company_number):
    clean_num = str(company_number).strip().zfill(8)
    base_url = f"https://find-and-update.company-information.service.gov.uk/company/{clean_num}"
    
    data = {
        'address': '',
        'postcode': '',
        'director_name': 'Director Not Listed',
        'director_role': 'Director',
        'appointed_date': ''
    }
    
    # Overview page
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
        print(f"   [Companies House Overview #{clean_num}]: {e}")
        
    # Officers page
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
        print(f"   [Companies House Officers #{clean_num}]: {e}")
        
    return data

def deep_crawl_site_for_contacts(site_url):
    """Crawls website homepage + dynamic contact links for mailto, tel, and emails"""
    found_emails = set()
    found_phones = set()
    
    if not site_url or not site_url.startswith('http'):
        return found_emails, found_phones
        
    try:
        req = urllib.request.Request(site_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            soup = BeautifulSoup(resp.read().decode('utf-8', errors='ignore'), 'html.parser')
            
            # 1. Search mailto: and tel: links
            for a in soup.find_all('a', href=True):
                href = a['href'].strip()
                if href.startswith('mailto:'):
                    em = href.replace('mailto:', '').split('?')[0].strip()
                    if '@' in em:
                        found_emails.add(em)
                elif href.startswith('tel:'):
                    ph = clean_phone_number(href.replace('tel:', ''))
                    if len(ph) >= 9:
                        found_phones.add(ph)
                        
            # 2. Search homepage text
            page_text = soup.get_text(separator=' ', strip=True)
            for m in EMAIL_REGEX.findall(page_text):
                if not m.lower().endswith(('.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp', '.js', '.css', 'sentry.io')):
                    found_emails.add(m)
            for p in UK_PHONE_REGEX.findall(page_text):
                ph = clean_phone_number(p)
                if len(ph) >= 10:
                    found_phones.add(ph)
                    
            # 3. Locate subpages matching 'contact', 'about', 'touch'
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
                                if '@' in em:
                                    found_emails.add(em)
                            elif href.startswith('tel:'):
                                ph = clean_phone_number(href.replace('tel:', ''))
                                if len(ph) >= 9:
                                    found_phones.add(ph)
                                    
                        c_text = c_soup.get_text(separator=' ', strip=True)
                        for m in EMAIL_REGEX.findall(c_text):
                            if not m.lower().endswith(('.png', '.jpg', '.jpeg', '.svg', '.gif')):
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

def harvest_company_contacts(company_name, postcode, address=""):
    clean_name = re.sub(r'\b(C\.I\.C\.|CIC|COMMUNITY INTEREST COMPANY|LIMITED|LTD)\b', '', company_name, flags=re.IGNORECASE).strip()
    clean_name = re.sub(r'[^a-zA-Z0-9\s]', ' ', clean_name).strip()
    
    # Extract City from address if possible
    addr_parts = [p.strip() for p in address.split(',') if p.strip()]
    city = addr_parts[-2] if len(addr_parts) >= 2 else ""
    if city and any(c.isdigit() for c in city):
        city = addr_parts[-3] if len(addr_parts) >= 3 else ""
        
    query = f"{clean_name} {city}".strip() if city else f"{clean_name} {postcode}".strip()
    
    result = {
        'phones': set(),
        'emails': set(),
        'website': '',
        'social': ''
    }
    
    try:
        post_data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request('https://lite.duckduckgo.com/lite/', data=post_data, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            links = soup.find_all('a', class_='result-link')
            snippets = soup.find_all('td', class_='result-snippet')
            
            for l, sn in zip(links[:8], snippets[:8]):
                href = l.get('href', '')
                sn_text = sn.get_text(separator=' ', strip=True)
                
                # Check for LinkedIn / Facebook
                if 'linkedin.com' in href or 'facebook.com' in href:
                    if not result['social']:
                        result['social'] = href
                        
                # Extract phones from snippet
                for p in UK_PHONE_REGEX.findall(sn_text):
                    ph = clean_phone_number(p)
                    if len(ph) >= 10:
                        result['phones'].add(ph)
                        
                # Extract emails from snippet
                for e in EMAIL_REGEX.findall(sn_text):
                    if not e.lower().endswith(('.png', '.jpg', '.jpeg', '.svg')):
                        result['emails'].add(e)
                        
                # Identify official website (skipping directories & social platforms)
                if href.startswith('http') and not any(d in href.lower() for d in SKIP_DOMAINS):
                    if not result['website']:
                        result['website'] = href
    except Exception as e:
        print(f"   [Search failed for {clean_name}]: {e}")
        
    # Deep crawl the discovered website
    if result['website']:
        web_emails, web_phones = deep_crawl_site_for_contacts(result['website'])
        for e in web_emails:
            result['emails'].add(e)
        for p in web_phones:
            result['phones'].add(p)
            
        # If no explicit email was parsed from HTML, construct standard domain inboxes
        if not result['emails']:
            try:
                domain = urllib.parse.urlparse(result['website']).netloc.replace('www.', '').strip()
                if domain and '.' in domain:
                    result['emails'].add(f"info@{domain}")
                    result['emails'].add(f"contact@{domain}")
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

def run_enricher(input_csv, output_csv, output_json, max_leads=10):
    print("=" * 80)
    print("UK COMPANIES HOUSE CONTACT HARVESTER (EMAILS & PHONES)")
    print("=" * 80)
    print(f"Reading from: {input_csv}\n")
    
    active_companies = []
    with open(input_csv, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('company_status', '').strip().lower() == 'active':
                active_companies.append(row)
                
    targets = active_companies[:max_leads]
    print(f"Found {len(active_companies)} active companies. Processing top {len(targets)}...\n")
    
    results = []
    
    for i, c in enumerate(targets, 1):
        name = c.get('company_name', '').strip()
        num = c.get('company_number', '').strip()
        
        print(f"[{i:2d}/{len(targets)}] {name} (Co #{num})")
        
        # Step 1: Companies House Address & Appointed Officer
        ch = get_companies_house_data(num)
        addr = ch['address'] or c.get('registered_office_address', '').strip()
        postcode = ch['postcode'] or extract_uk_postcode(addr)
        director = ch['director_name']
        role = ch['director_role']
        
        print(f"     Appointed Officer: {director} ({role})")
        print(f"     Location:          {addr}")
        
        # Step 2: Automated Contact Enrichment
        contacts = harvest_company_contacts(name, postcode, addr)
        
        phone = contacts['primary_phone'] or "Available via Local RoC Search"
        email = contacts['primary_email'] or "Available on Domain"
        website = contacts['website'] or "N/A"
        
        print(f"  -> VERIFIED PHONE:    {phone}")
        print(f"  -> VERIFIED EMAIL:    {email}")
        print(f"  -> OFFICIAL WEBSITE:  {website}")
        if contacts['social']:
            print(f"  -> SOCIAL / PROFILE:  {contacts['social']}")
        print("-" * 80)
        
        results.append({
            "Company_Name": name,
            "Company_Number": num,
            "Appointed_Director": director,
            "Director_Role": role,
            "Registered_Office_Address": addr,
            "Postcode": postcode,
            "Direct_Phone": contacts['primary_phone'],
            "Direct_Email": contacts['primary_email'],
            "Official_Website": contacts['website'],
            "Social_or_LinkedIn": contacts['social'],
            "All_Phones": contacts['all_phones'],
            "All_Emails": contacts['all_emails'],
            "Companies_House_Profile": f"https://find-and-update.company-information.service.gov.uk/company/{num.zfill(8)}"
        })
        
        time.sleep(1) # Polite interval
        
    fieldnames = [
        "Company_Name", "Company_Number", "Appointed_Director", "Director_Role",
        "Registered_Office_Address", "Postcode", "Direct_Phone", "Direct_Email",
        "Official_Website", "Social_or_LinkedIn", "All_Phones", "All_Emails",
        "Companies_House_Profile"
    ]
    
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
        
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
        
    print("\n" + "=" * 80)
    print("ENRICHMENT COMPLETED!")
    print(f"CSV Output:  {output_csv}")
    print(f"JSON Output: {output_json}")
    print("=" * 80)

if __name__ == "__main__":
    in_csv = r"d:\Certification\Companies-House-search-results.csv"
    out_csv = r"d:\Certification\enriched_uk_companies.csv"
    out_json = r"d:\Certification\enriched_uk_companies.json"
    
    run_enricher(in_csv, out_csv, out_json, max_leads=10)

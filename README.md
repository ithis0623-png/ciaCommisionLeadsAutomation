# 🚀 UK Companies House Automated Contact Scraper & Enricher

An automated, production-grade 2-step data pipeline for scraping **UK Companies House** records, extracting appointed living directors, and enriching each company with **verified direct phone numbers, business email addresses, official websites, and LinkedIn/social profiles**.

Designed for automated batch processing across large datasets (~5,000 companies) with **streaming append**, **automatic checkpointing**, **dual logging**, and **scheduled execution via GitHub Actions**.

---

## 📌 Architecture: The 2-Step Methodology

```
Step 1: UK Companies House                     Step 2: Automated Contact Enrichment
┌─────────────────────────────────┐           ┌───────────────────────────────────────────────┐
│ • Company Name & Number         │           │ • Clean Business Name + Postcode / City       │
│ • Living Appointed Director     │ ────────> │ • DuckDuckGo Lite & Deep Website Crawler      │
│ • Uncorrupted Registered Office │           │ • Extract: Direct Phone, Work Email, Website  │
└─────────────────────────────────┘           └───────────────────────────────────────────────┘
```

1. **Step 1 (Official Registry Extraction):**
   * Extracts clean registered office address and postcode directly from the official Companies House overview page.
   * Scrapes `/officers` to pull the active appointed director/secretary's full legal name, role, and appointment date.
2. **Step 2 (Automated Contact Enrichment):**
   * Automatically searches public business footprints using the company name and postcode/city.
   * Identifies the official business website (filtering out general registries like Endole, 192.com, Yell).
   * Deep-crawls the homepage and `/contact` subpages to extract:
     * **`<a href="tel:...">`** and UK landline / mobile regex (`07...`, `01...`, `+44...`).
     * **`<a href="mailto:...">`** and verified contact inboxes.
     * LinkedIn company pages and director social profiles.

---

## 📊 Live Enriched Output

* **CSV Spreadsheet:** [`enriched_uk_companies.csv`](./enriched_uk_companies.csv) (auto-appended after every record).
* **JSON Dataset:** [`enriched_uk_companies.json`](./enriched_uk_companies.json).
* **Checkpoint Tracker:** [`checkpoint.json`](./checkpoint.json) (tracks already processed company numbers so no work is duplicated).
* **Execution Log File:** [`scraper.log`](./scraper.log) (timestamped progress & diagnostic logs).

---

## 🔍 How to View Logs After Deployment (GitHub Actions)

You can monitor the live execution and review historical logs anytime directly on GitHub:

1. Go to your repository on GitHub: [`https://github.com/ithis0623-png/ciaCommisionLeadsAutomation`](https://github.com/ithis0623-png/ciaCommisionLeadsAutomation).
2. Click the **"Actions"** tab at the top.
3. Click on the latest workflow run (e.g., *Automated UK Companies House Scraper & Enricher*).
4. **Live Console Logs:** Click on the `scrape-and-enrich` job to see real-time streaming output for every company:
   ```text
   [14/50] Scraping: STC - SAFEGUARDING THROUGH COMMUNITIES C.I.C. (Co #12613426)
         Appointed Officer: AKRAM, Waseem (Secretary)
         Location:          Ash House, Goulbourne Street, Keighley, England, BD21 1PG
      📞 DIRECT PHONE:    01535 288102
      ✉️ DIRECT EMAIL:    Contact@stcommunities.co.uk
      🌐 WEBSITE:         https://www.stcommunities.co.uk/
   ```
5. **Downloadable Log Artifacts:** Under the workflow run summary, download the **`scraper-execution-log`** zip file containing the complete `scraper.log` file.
6. **Visual Progress Summary:** A rich Markdown table summarizing the newly discovered emails and phone numbers is displayed directly on the run summary page.

---

## ⚙️ Triggering Scraper Runs

### A. Automatic Triggers
* **On Every Push:** The scraper automatically triggers whenever code is pushed to `main`.
* **Recurring Schedule:** Automatically runs every 4 hours (`cron: '0 */4 * * *'`) in polite batches to continuously chew through the 5,000 dataset without hitting rate limits.

### B. Manual Trigger via GitHub UI
1. Go to the **Actions** tab on GitHub.
2. Select **"Automated UK Companies House Scraper & Enricher"** in the left sidebar.
3. Click the **"Run workflow"** button.
4. (Optional) Customize the batch size (default: `50` companies) and click **Run workflow**.

---

## 💻 Running Locally

To run or test on your local machine:

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the scraper in batches (e.g., 20 companies)
python scraper.py --batch-size 20

# 3. Process all remaining companies continuously
python scraper.py --batch-size 0
```

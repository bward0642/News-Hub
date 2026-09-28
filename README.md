# Financial Literacy News Hub
**Sokolov-Miller Family Financial & Life Skills Center — staff briefing site**

A free website that collects the week's news on **student loans, FAFSA & financial aid, consumer credit & banking, and financial-literacy teaching**. It updates itself every Monday morning.

- **Cost:** $0. It is hosted on GitHub Pages and updated by GitHub Actions, and there are no paid services or API keys.
- **Sources:** StudentAid.gov, the Federal Register (Dept. of Education & CFPB rules), CFPB, FTC, the Federal Reserve, FDIC, MyMoney.gov, Jump$tart, the Council for Economic Education, EVERFI, the National Consumer Law Center, TICAS, Inside Higher Ed, and U.S. news coverage through Google News.
- **Features:**
  - a "Heads up" box for deadlines and rule changes
  - key dates pulled from federal rules
  - topic filters and search
  - an archive of every past week
  - staff notes and teaching tips
  - a printable layout for team meetings

---

## One-time setup (about 15 minutes, no coding)

### 1. Create the repository
1. Sign up for a free account at <https://github.com>. An organization account for the center works well.
2. Click **+** (top right) → **New repository**.
3. Name it something like `news-hub`, choose **Public** (required for free hosting), check **Add a README file**, and click **Create repository**.

### 2. Upload the project files
1. In the new repository, click **Add file → Upload files**.
2. Drag in these folders and files from this project: `config`, `data`, `scripts`, `static`, `templates`, `requirements.txt`, `README.md`, `.gitignore`.
   *(On a Mac, `.gitignore` is hidden in Finder. Press ⌘ ⇧ . to show hidden files, or skip it; it's optional.)*
3. Click **Commit changes**.

### 3. Add the weekly update automation
Mac Finder hides the `.github` folder, so create this file by hand:
1. Click **Add file → Create new file**.
2. For the name, type exactly: `.github/workflows/weekly-update.yml`
3. Open `.github/workflows/weekly-update.yml` from this project, copy everything, and paste it in.
4. Click **Commit changes**.

### 4. Turn on the website
1. Go to **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Go to the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
4. Click **Weekly update** → **Run workflow** → **Run workflow**.
5. After 2–3 minutes, the site address appears in **Settings → Pages**. It will look like `https://YOUR-ACCOUNT.github.io/news-hub/`. Share that link with staff.

From then on it updates **every Monday around 6 AM Eastern**, with no action needed.

---

## Everyday use

### Add a staff note ("What this means for our clients")
1. On the site, click **＋ Add staff note** on any item. A ready-made snippet is copied.
2. On GitHub, open `config/staff_notes.yaml` and click the ✏️ pencil icon.
3. Paste the snippet under `notes:` (replace `notes: []` with `notes:` the first time), then write your note.
4. Click **Commit changes**. The site rebuilds in about a minute.

`pin: true` puts the note at the top of the home page. Change it to `pin: false` (or delete the entry) when it's no longer needed.

### Add a teaching tip
In the same file, copy an existing entry under `tips:` and edit it. The three newest tips appear on the home page, and all of them appear on the Resources page.

### Update right now
**Actions → Weekly update → Run workflow.**

### Tune what shows up (all in `config/`, edited the same way)
| File | What it controls |
|---|---|
| `topics.yaml` | Keywords for each topic, plus the words that trigger a "Heads up" badge |
| `sources.yaml` | Feeds and news searches, plus lists of blocked outlets and skipped titles |
| `resources.yaml` | Links on the Resources page and the "Check these sites weekly" box |
| `staff_notes.yaml` | Staff notes and teaching tips |

Tips:
- If you're seeing too many off-topic stories, add the outlet to `exclude_publishers` or a word to `exclude_titles` in `sources.yaml`.
- If something important is missed, add a keyword to `topics.yaml`.

---

## Good to know
- **Accuracy:** items are gathered and sorted automatically by keyword and are not reviewed. Always confirm details on the official source before advising a client. The site footer says this too.
- **"News" vs "Official":** News items come from Google News and are useful for context. Official items come straight from agencies and established nonprofits.
- **If a source stops working**, the rest still update, and the site footer lists the source that failed. If *every* source fails, GitHub emails the account owner.
- **Inactivity:** GitHub pauses scheduled jobs in repositories with no activity for 60 days. The weekly update saves its results to the repository, which counts as activity. If the job ever gets paused, re-enable it on the **Actions** tab.
- **Adding AI summaries later:** the fetch script can be extended to write plain-language summaries with the Claude API. This costs roughly $1–3/month.

## For technical staff
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/fetch_news.py          # add --days 30 to backfill
.venv/bin/python scripts/build_site.py          # writes ./site
python3 -m http.server 8000 --directory site    # preview at http://localhost:8000
```

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from nltk.data import split_resource_url
from playwright.sync_api import sync_playwright
from ML.main import KeyWords, ReturnSimilatity
from random import randint
from deep_translator import GoogleTranslator
from collections import Counter
from urllib.parse import urlencode
import re
import time
import psycopg2
import os
from dotenv import load_dotenv

def resource_path(*parts):
    base_dir = getattr(sys, "_MEIPASS", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    return os.path.join(base_dir, *parts)


env_file = resource_path(".env")
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

# --------------------------------------------------
#   I used the Codex to fix some bugs on the project 
# --------------------------------------------------

conn = psycopg2.connect(
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
)

cursor = conn.cursor()

def _ensure_search_errors():
    query = """
        CREATE TABLE IF NOT EXISTS search_errors(
            id SERIAL PRIMARY KEY,
            bad_search_text TEXT
        );    
    """
    cursor.execute(query)

def generate_url(errors: int, search_error: str):

    # --------------------------------
    # Generating the search sentence
    # --------------------------------
    
    search_errors = errors
    bad_search = search_error

    keywords = KeyWords()
    first_index = randint(0, len(keywords)-1) 
    second_index = randint(0, len(keywords)-1) 
    while second_index == first_index:
        another_second_index = randint(0, len(keywords)-1)
        if another_second_index != first_index:
            second_index = another_second_index
            break
        continue
    first_word = keywords[first_index]
    second_word = keywords[second_index]
    search_sentence = "" + first_word[0] + " " + second_word[0]
    translated_sentence = GoogleTranslator(source='pt', target='en').translate(search_sentence)

    if search_errors >=1:
        final_search_sentence =  f"lead energy engineer solar and wind power"
        cursor.execute("INSERT INTO search_errors(bad_search_result) VALUES (%s)", (bad_search) )
        conn.commit()
    else:
        final_search_sentence = f"lead energy engineer {translated_sentence} solar and wind power "

    lower_sentence = final_search_sentence.lower()
    split_sentence = lower_sentence.split()
    ocurrences = {}
    for word in split_sentence:
        if word in ocurrences:
            lower_sentence = re.sub(f'{word}', ' ', lower_sentence)
            # Apearly, by deleting one ocurrence, we take all the ocurrences off. As I don't wanna this
            # I'll add the word once.
            lower_sentence += word
        else:
            ocurrences[word] = 1
    final_search_sentence = re.sub("  ", " ", lower_sentence)

    # ---------------------------------
    # Preferences config
    # ---------------------------------

    base_url = "https://www.linkedin.com/jobs/search/"

    locations = [
        "United States","Canada", "United Kingdom"
    ]
    draw_any_place = randint(0,len(locations)-1)
    locate = locations[draw_any_place]

    params = {
        "keywords": final_search_sentence,
        "location": [
            "United States","Canada", "Toronto", "New York", "Calgary", "Vancouver", "San Francisco", "Otawwa"
        ],
        "f_wt": ",".join(["1", "3"]),
        "f_E": ",".join(["4", "5", "6"])
    }

    url = f"{base_url}?{urlencode(params)}"
    return url


def create_url(errors: int, search_error: str):

    search_errors = errors
    bad_search = search_error

    url = generate_url(search_errors, bad_search)
    _ensure_search_errors()
    query = """
            SELECT * FROM search_errors
            """
    cursor.execute(query)
    bad_searchs = cursor.fetchall()
    while url in bad_searchs:
        new_url = generate_url(search_errors, bad_search)
        if new_url in bad_searchs:
            continue
        else:
            url = new_url
            break
    return url

def normalize_title(title):
    title = title or ""
    title = title.strip().lower()
    return re.sub(r"\s+", " ", title)


def is_the_title_in_blacklist(title):
    normalized_title = normalize_title(title)
    blacklist = [
        "mechanical",
        "mechanical engineer",
        "mechanical engineering",
        "electrical engineer",
        "electrical engineering",
        "thermal",
        "thermal engineering",
        "thermal engineer"
    ]

    for blocked_title in blacklist:
        if re.search(rf"\b{re.escape(blocked_title)}\b", normalized_title):
            return True

    return False

def BrowsingForJobs():


    with sync_playwright() as playwright:

        # ------------------------------------------
        # Launching the browser and going to the url
        # ------------------------------------------
         
        # The user must have installed the Google Chrome for runs out
        browser = playwright.chromium.launch(channel="chrome",headless=True)
        page = browser.new_page()
        page.set_default_timeout(10000)  
        url = create_url(0, "")
        page.goto(url)
        print("\n", "------- URL: ", url, "-----------","\n")
        page.wait_for_timeout(5000)

        print(" ----------- test1")
        try:
            does_not_match = page.locator('xpath=//*[@id="main-content"]/section[1]/h1')
            i=1
            urls_generated = [""]
            while does_not_match.text_content():
                new_url = create_url(i, urls_generated[-1])
                page.goto(new_url)
                page.wait_for_timeout(5000)
                i+=1
                if does_not_match.text_content():
                    print("There's still an error")
                    urls_generated.append(new_url)
                    continue 
                else:
                    print("Getting out from while loop.")
                    break
        except:
            pass
        print(" ----------- test2")
        # ---------------------------------------------------------------------
        # Getting data from Linkeding and handling them to store into the table
        # ---------------------------------------------------------------------

        cards = page.locator(".job-search-card")
        job_ids = []
        for i in range(cards.count()):
            card = cards.nth(i)
            urn = card.get_attribute("data-entity-urn")
        
            if urn is not None:
                job_id = urn.split(":")[-1]
                job_ids.append(job_id)

        jobs_oppenings = []
        for job_id in job_ids:
            jobs_oppenings.append(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")

        cursor.execute("TRUNCATE TABLE jobs RESTART IDENTITY")
        conn.commit()

        print(" ----------- test3")
        final_jobs_dicts_list = []
        jobs_titles = set()
        for job in jobs_oppenings:
            new_page = browser.new_page()
            new_page.goto(job)
            page.wait_for_timeout(5000)

            try:
                title = normalize_title(new_page.locator('.top-card-layout__title').text_content())
                if is_the_title_in_blacklist(title) or title in jobs_titles:
                    print("-----moving on...")
                    continue
                print("Nothing went wrong with the job title")
                location = new_page.locator('xpath=/html/body/section/div/div[1]/div/h4/div[1]/span[2]').text_content()
                company = new_page.locator('.topcard__org-name-link').text_content()
                job_text = new_page.locator('xpath=/html/body/div/section[1]/div/div/section/div').text_content()

                def split_text(text, max_len=4999):
                    return [text[i:i+max_len] for i in range(0, len(text), max_len)]

                translated_chunks = []
                for chunk in split_text(job_text):
                    translated = GoogleTranslator(source='en', target='pt').translate(chunk)
                    translated_chunks.append(translated)
                job_text = ''.join(translated_chunks)
                similarity = ReturnSimilatity(job_text)
                try:
                    logo = new_page.locator('xpath=/html/body/section/div/a/img').get_attribute("data-delayed-url")
                except:
                    logo = ""
                try:
                    salary = re.findall(r'\$\d+(?:,\d{3})*(?:\.\d+)?', job_text)
                except:
                    salary = "Not found"
                print(f'The company: {company} \n Got this similarity: {similarity} \n')
                if not similarity >= 0.6:
                    continue
                jobs_titles.add(title)
                try:
                    new_page.locator('.top-card-layout__title').click()
                    new_page.wait_for_timeout(5000)
                    job_link = new_page.url
                except:
                    job_link = job                  
                
                final_jobs_dicts_list.append({
                    "title": title,
                    "location": location,
                    "company": company,
                    "job_text": job_text,
                    "job_link": job_link,
                    "similarity": similarity,
                    "logo": logo,
                })
                if len(final_jobs_dicts_list) == 60:
                    break
                try:
                    cursor.execute(
                        """
                        INSERT INTO jobs (
                            company_name,
                            job_title,
                            description,
                            locate,
                            url,
                            company_logo_path,
                            similarity
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            company,
                            title,
                            job_text,
                            location,
                            job_link,
                            logo,
                            similarity
                        )
                    )
                    conn.commit()
                    print("Everything has worked!")
                except Exception as e:
                    print(f'Error: {e}')
                    continue

            except Exception as e:
                print(f"Let's try the next one. But see the error: {e}")
                continue
            finally:
                new_page.close()
        browser.close()

if __name__ == "__main__":
    BrowsingForJobs()
    conn.close()

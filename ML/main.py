import psycopg2
import os
from dotenv import load_dotenv
from docx import Document
import spacy
import numpy as np
import re   
from collections import Counter
import nltk
import unicodedata
from nltk.corpus import stopwords
from io import BytesIO

"""

    In this document, I'm fetching the keywords at a curriculum
    and will send these to the database and WebScrapping process.

    In the webscrapping process, I'll use this selected words to 
    browse for jobs in the web.

    quick note: I know that this process is not the best way to do it, 
    but it's a good start for me to understand how to work with NLP 
    and webscrapping.

    And I'm sure that I need to improve my knowledges on this libs:

    - NLTK
    - collections
    - regex
    - io
    - unicodedata

"""

load_dotenv()
nltk.download("stopwords")
nltk.download("punkt_tab")
stopwords_pt = set(stopwords.words('portuguese'))
stopwords_en = set(stopwords.words('english'))
stopwords_en.discard("not")
nlp = spacy.load("pt_core_news_md")

conn = psycopg2.connect(
        database= f"{os.getenv("DB_NAME")}",
        user    = f"{os.getenv("DB_USER")}",
        password= f"{os.getenv("DB_PASSWORD")}",
        host    = f"{os.getenv("DB_HOST")}",
        port    = f"{os.getenv("DB_PORT")}"
)

cursor = conn.cursor()

cursor.execute("SELECT DISTINCT file_data FROM curriculum")
curriculum = cursor.fetchone()[0]
document = Document(BytesIO(curriculum))

# TO DO: apply SRP
def ReadfileBytes():
    
    text = "\n".join(sentence.text for sentence in document.paragraphs)

    # refining the text to remove unnecesary spaces between important words
    text = re.sub(r'\n+', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = unicodedata.normalize('NFKC', text)

    pattern = [
        r"page \d+",
        r'page \d+ of \d+'
    ]
    for i in pattern:
        text = re.sub(i, "", text)
    text.lower()
        

    unnecessary_words = [
        "time",
        "desenvolver",
        "trabalhar",
        "trabalhei",
        "projeto",
        "profissional",
        "Empresa",
        "saneamento",
    ]

    for i in unnecessary_words:
        text = re.sub(i, "", text)
    doc = nlp(text)

    new_text = " ".join(token.lemma_ for token in doc)
    doc = nlp(new_text)
    return doc

def ReturnSimilatity(document):
    # Here, we"ve already found some 15 jobs opening. That'd be the last thing left, in theory.
    JobOpeningContent = document
    JobOpeningContent = re.sub(r'\n+', '\n', JobOpeningContent)
    JobOpeningContent = re.sub(r'[ \t]+', ' ', JobOpeningContent)
    JobOpeningContent = unicodedata.normalize('NFKC', JobOpeningContent)

    token = nltk.word_tokenize(JobOpeningContent.lower(), language="portuguese")

    result = [
        word for word in token
        if word.isalpha() and word not in stopwords_pt
    ]
    
    refined_text = " ".join(result)
    doc2 = nlp(refined_text)
    
    new_text2 = " ".join(token.lemma_ for token in doc2)
    doc2= nlp(new_text2)
    doc1 = ReadfileBytes()
    similarity = doc1.similarity(doc2)
    return similarity

def KeyWords():
    text = ReadfileBytes()
    word_freq = Counter(token.text for token in text if token.is_alpha and token.text not in stopwords_pt)
    most_common_words = word_freq.most_common(10)
    comprehension = [word for word in most_common_words if len(word[0]) >2]
    return comprehension

conn.close()

if __name__ == "__main__":
    ReturnSimilatity()

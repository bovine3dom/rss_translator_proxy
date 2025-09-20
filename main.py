import os
import re
import uuid
import deepl
import feedparser
import requests
import time
from datetime import datetime, timezone
from flask import Flask, request, Response
from feedgen.feed import FeedGenerator
from bs4 import BeautifulSoup
from diskcache import Cache
from urllib.parse import urlparse

# disable SSL warnings
import urllib3
from urllib3.exceptions import InsecureRequestWarning
urllib3.disable_warnings(InsecureRequestWarning)

DEEPL_AUTH_KEY = os.environ.get("DEEPL_AUTH_KEY")
if not DEEPL_AUTH_KEY:
    raise ValueError("DEEPL_AUTH_KEY environment variable not set.")

AZURE_TRANSLATOR_KEY = os.environ.get("AZURE_TRANSLATOR_KEY")
AZURE_TRANSLATOR_ENDPOINT = os.environ.get("AZURE_TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com")
AZURE_TRANSLATOR_REGION = os.environ.get("AZURE_TRANSLATOR_REGION")
ALWAYS_AZURE_TITLE_SUBSTRINGS = [s.strip().lower() for s in os.environ.get("ALWAYS_AZURE_TITLE_SUBSTRINGS", "").split(",") if s.strip()]
ALWAYS_AZURE_FEEDS = set(host.strip().lower() for host in os.environ.get("ALWAYS_AZURE_FEEDS", "").split(",") if host.strip())

try:
    translator = deepl.Translator(DEEPL_AUTH_KEY)
except ValueError as e:
    raise ValueError(f"DeepL authentication error: {e}") from e

cache = Cache('.translation_cache', size_limit=1024 * 1024 * 1024) # 1GB, measured in bytes

app = Flask(__name__)

def is_always_azure(feed_info) -> bool:
    title_html = feed_info.get('title', '') or ''
    title_text = BeautifulSoup(title_html, "html.parser").get_text(strip=True).lower()
    if any(sub in title_text for sub in ALWAYS_AZURE_TITLE_SUBSTRINGS):
        return True
    link = (feed_info.get('link') or '').strip().lower()
    try:
        host = urlparse(link).netloc.lower()
    except Exception:
        host = ""
    if not ALWAYS_AZURE_FEEDS:
        return False
    if (host and host in ALWAYS_AZURE_FEEDS) or (link and link in ALWAYS_AZURE_FEEDS):
        return True
    if any(tok in link or (host and tok in host) for tok in ALWAYS_AZURE_FEEDS):
        return True
    return False

@cache.memoize()
def azure_translate(text_to_translate: str, lang: str) -> str:

    # this is stupid duplication of getTranslation, but i am lazy
    original_length = len(text_to_translate)
    truncated_to_500_chars = text_to_translate[:500]
    sentences = re.split(r'(?<=[.?!])\s+', text_to_translate)
    if len(sentences) > 2:
        two_sentences = " ".join(sentences[:2])
    else:
        two_sentences = text_to_translate

    if len(two_sentences) < len(truncated_to_500_chars):
        maybe_truncated = two_sentences
    else:
        maybe_truncated = truncated_to_500_chars

    if len(maybe_truncated) < original_length:
        maybe_truncated += " [...]"
    # stupid duplication ends

    url = AZURE_TRANSLATOR_ENDPOINT.rstrip('/') + '/translate'
    params = {'api-version': '3.0', 'to': [lang]}
    headers = {
        'Ocp-Apim-Subscription-Key': AZURE_TRANSLATOR_KEY,
        'Content-type': 'application/json',
        'X-ClientTraceId': str(uuid.uuid4()),
    }
    if AZURE_TRANSLATOR_REGION:
        headers['Ocp-Apim-Subscription-Region'] = AZURE_TRANSLATOR_REGION
    r = requests.post(url, params=params, headers=headers, json=[{'text': maybe_truncated}], timeout=15)
    if not r.ok:
        raise Exception(f"Azure translation failed with status code {r.status_code}")
    j = r.json()
    return j[0]['translations'][0]['text']

@cache.memoize()
def getTranslation(text_to_translate: str, target_lang: str = "EN-GB") -> str:
    if not isinstance(text_to_translate, str) or not text_to_translate.strip():
        return text_to_translate

    # cut string down to least of two sentences/500 chars
    original_length = len(text_to_translate)
    truncated_to_500_chars = text_to_translate[:500]
    sentences = re.split(r'(?<=[.?!])\s+', text_to_translate)
    if len(sentences) > 2:
        two_sentences = " ".join(sentences[:2])
    else:
        two_sentences = text_to_translate

    if len(two_sentences) < len(truncated_to_500_chars):
        maybe_truncated = two_sentences
    else:
        maybe_truncated = truncated_to_500_chars

    if len(maybe_truncated) < original_length:
        maybe_truncated += " [...]"

    # send for translation
    try:
        result = translator.translate_text(maybe_truncated, target_lang=target_lang)
        return result.text
    except deepl.DeepLException as e:
        app.logger.error(f"DeepL API error: {e}")
        return azure_translate(maybe_truncated, target_lang) or text_to_translate
    except Exception as e:
        app.logger.error(f"An unexpected error occurred during translation: {e}")
        return azure_translate(maybe_truncated, target_lang) or text_to_translate


@app.route('/feed')
def get_feed():
    feed_url = request.args.get('url')
    target_lang = request.args.get('lang', 'EN-GB').upper() # let user set target language


    if not feed_url:
        return "Please provide a 'url' query parameter.", 400

    try:
        # some websites hate the default python requests agent. others hate curl...
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0"
        }
        response = requests.get(feed_url, timeout=10, headers=headers, verify=False) # ssl disabled because these random feeds are bad at it
        response.raise_for_status()
        
        original_feed = feedparser.parse(response.content)
        if original_feed.bozo:
            return f"The provided URL does not point to a well-formed RSS feed. Error: {original_feed.bozo_exception}", 400

        fg = FeedGenerator()
        
        feed_info = original_feed.feed
        force_azure = False
        try:
            force_azure = is_always_azure(feed_info)
        except Exception:
            pass
        def _T(s: str, lang: str):
            if force_azure:
                try:
                    return azure_translate(s, lang)
                except Exception as e:
                    app.logger.error(f"Azure translation failed: {e}")
                    return s
            return getTranslation(s, target_lang=lang)
        
        channel_title_html = feed_info.get('title', 'Untitled Feed')
        channel_title_text = BeautifulSoup(channel_title_html, "html.parser").get_text(strip=True)
        translated_channel_title = _T(channel_title_text, target_lang)
        fg.title(translated_channel_title)
        
        channel_desc_html = feed_info.get('description') or feed_info.get('subtitle', '')
        channel_desc_text = BeautifulSoup(channel_desc_html, "html.parser").get_text(strip=True)
        translated_channel_desc = _T(channel_desc_text, target_lang)
        
        if not translated_channel_desc.strip():
            translated_channel_desc = translated_channel_title
        fg.description(translated_channel_desc)
        
        fg.link(href=feed_info.get('link', ''), rel='alternate')
        fg.id(feed_info.get('id', feed_info.get('link', '')))
        if 'language' in feed_info:
            fg.language(feed_info.language)

        for entry in original_feed.entries:
            fe = fg.add_entry()
            
            item_title_html = entry.get('title', 'No Title')
            item_title_text = BeautifulSoup(item_title_html, "html.parser").get_text(strip=True)
            translated_item_title = _T(item_title_text, target_lang)
            fe.title(translated_item_title)

            description_html = entry.get('description') or entry.get('summary', '')
            description_text = BeautifulSoup(description_html, "html.parser").get_text(separator=' ', strip=True)
            translated_description = _T(description_text, target_lang)

            if not translated_description.strip():
                translated_description = translated_item_title
            fe.description(translated_description)

            if 'author' in entry:
                author_text = BeautifulSoup(entry.author, "html.parser").get_text(strip=True)
                fe.author(name=_T(author_text, target_lang))
                
            fe.link(href=entry.get('link', ''))
            fe.guid(entry.get('id', entry.get('link', '')), permalink=False)
            
            if 'published_parsed' in entry and entry.published_parsed:
                dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                fe.pubDate(dt)

        translated_rss_feed = fg.rss_str(pretty=True)
        return Response(translated_rss_feed, mimetype='application/rss+xml')
    except Exception as e:
        app.logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        return "An internal error occurred while processing the feed.", 500


def main():
    app.run(debug=True, host='0.0.0.0', port=5000)

if __name__ == '__main__':
    main()

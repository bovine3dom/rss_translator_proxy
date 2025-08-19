# RSS translator proxy

Simple proxy that takes a URL to an RSS feed and returns a feed which has been stripped of HTML and mostly translated using DeepL's API.

SSL is unverified because I found that a fair few feeds had it misconfigured and I don't care very much if people see that I like to read about trains

# Running

```
uv sync
env DEEPL_AUTH_KEY='your key goes here' uv run -- gunicorn --workers 4 --timeout 240 --bind 0.0.0.0:5000 main:app
```

# Usage

```
curl http://localhost:5000/feed?url=https://ferrovie.info/?format=feed&type=rss
```

Add e.g. '&lang=de' to change the target language from en-gb

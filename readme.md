# RSS translator proxy

Simple proxy that takes a URL to an RSS feed and returns a feed which has been stripped of HTML and mostly translated using DeepL's API.

SSL is unverified because I found that a fair few feeds had it misconfigured and I don't care very much if people see that I like to read about trains

# Running

## From code

```
uv sync
env DEEPL_AUTH_KEY='your key goes here' uv run -- gunicorn --workers 4 --timeout 240 --bind 0.0.0.0:5000 main:app
```

If you'd like to fall back to Azure, set the `AZURE_TRANSLATOR_KEY` and `AZURE_TRANSLATOR_REGION` environment variables.

You may wish to add particularly prolific feeds to the `ALWAYS_AZURE_FEEDS` environment variable, e.g. `ALWAYS_AZURE_FEEDS=lok-report.de,another-noisy-feed.com` because it has a much bigger free tier.

## Using docker

**Start the container with `docker run`**

```
docker run -d \
  --name rss_translator_proxy \
  -e DEEPL_AUTH_KEY='your key goes here'
  -p 5000:5000 \
  --restart=unless-stopped \
  ghcr.io/bovine3dom/rss_translator_proxy:latest
```

**or `docker-compose`**

```yaml
services:
  rss_translator_proxy:
    image: ghcr.io/bovine3dom/rss_translator_proxy:latest
    container_name: rss_translator_proxy
    ports:
      - 5000:5000
    environment:
      - DEEPL_AUTH_KEY='your key goes here'
    restart: unless-stopped
```

# Usage

```
curl http://localhost:5000/feed?url=https://ferrovie.info/?format=feed&type=rss
```

Add e.g. '&lang=de' to change the target language from en-gb

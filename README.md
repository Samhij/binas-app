# Digitale Binas — lokale recreatie

Een zelfstandige, lokale Binas-tabelzoeker rond het door de gebruiker aangeleverde PDF-bestand.

## Starten

Open een terminal in deze map, zet je DeepSeek-sleutel en sitewachtwoord in `.env` (`DEEPSEEK_API_KEY=` en `SITE_PASSWORD=`), en start de lokale server:

```sh
python3 server.py
```

Open daarna <http://localhost:8080>.

`python3 -m http.server 8080` toont de app nog steeds, maar de AI-assistent heeft `server.py` nodig.

Direct openen als `file://` werkt niet volledig, omdat browsers lokale JSON-verzoeken blokkeren.

## Homelab + Cloudflare Tunnel

This directory includes a `Dockerfile` and `compose.yaml`. On the homelab host:

```sh
cd /path/to/binas-recreation
docker compose build
docker compose up -d binas
```

The included Nginx config explicitly serves JavaScript modules (`.mjs`) with the
correct MIME type required by PDF.js. If you update an older deployment, rebuild
the image so that `nginx.conf` is copied into the container:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

The app will listen on the homelab host's LAN address at port `8080`, for example `http://192.168.1.50:8080`.

Put your DeepSeek key and site password in `.env` as `DEEPSEEK_API_KEY=` and `SITE_PASSWORD=` before starting Compose. The file is excluded from the image and injected at runtime. Visitors see a password-only login screen until that password matches. Leave `SITE_PASSWORD` empty only if you want the site open.

Because `cloudflared` runs separately, create a remotely-managed tunnel in Cloudflare Zero Trust and add a published application route from the machine/container running `cloudflared`:

- Hostname: `binas.example.com` (use a domain in your Cloudflare account)
- Service: `http://192.168.1.50:8080` (replace with the homelab server's LAN IP)

Then start/restart your separate `cloudflared` connector using its existing deployment method. No tunnel token belongs in this app's repository or Compose file.

Before testing, allow TCP 8080 from the cloudflared host to the app host in the homelab firewall. You still do not need to port-forward 80/443 on your router: the tunnel connector makes the outbound Cloudflare connection.

## Functies

- 383 doorzoekbare tabel- en subtabelverwijzingen
- Filters voor natuurkunde, scheikunde, biologie en wiskunde
- Directe navigatie naar de juiste PDF-pagina
- De actieve tabel volgt automatisch mee wanneer je handmatig door de PDF scrolt
- Favorieten en recent bekeken items in lokale browseropslag
- Licht/donker thema
- Handmatige paginasprong en PDF in nieuw tabblad
- Wachtwoordscherm (`SITE_PASSWORD` in `.env`) vóór toegang tot de site
- Popup AI-assistent (DeepSeek) die Binas doorzoekt en naar de juiste PDF-pagina springt
- Responsive zijbalk voor mobiel

## Bestanden

- `assets/Binas.pdf` — het door de gebruiker aangeleverde PDF-bestand
- `data/navigation-data.json` — tabelindex
- `data/binas-align.json` — fijnere tabel/PDF-uitlijning
- `data/binas-pages.json` — doorzoekbare PDF-tekst per pagina
- `.env` — `DEEPSEEK_API_KEY` voor de assistent en `SITE_PASSWORD` voor de login (niet in de Docker-image)
- `server.py` — lokale webserver + DeepSeek-chat-API + sessiecookie
- `login.html` — wachtwoordscherm
- `index.html`, `styles.css`, `app.js`, `chat.js` — clean-room webapp
- `vendor/pdfjs/` — lokale PDF.js-renderer (geen internetverbinding nodig)

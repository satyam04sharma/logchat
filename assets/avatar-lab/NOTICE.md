# Landing-page credits and source

This standalone landing page uses Bible Strong Avatar Lab's Grok bot and animation library by Stéphane Montlouis-Calixte.

- Studio: https://avatars.bible-strong.app/
- Upstream source: https://github.com/smontlouis/bible-strong-avatar-lab
- Runtime: @bible-strong/avatar-web 0.1.0 and @bible-strong/avatar-core 0.1.0 (AGPL-3.0-only).
- License: [GNU AGPL v3](LICENSE).
- Complete landing-page source: https://github.com/satyam04sharma/logchat/tree/gh-pages/avatar-preview
- Runtime source is also included in landing.js.map, alongside the editable landing-entry.js and logchat.avatar.json.

Changes: selected Grok bot; bounded wake/curious/happy/celebrate/confused/listening reactions with a neutral ending; added page arrival and clipboard interactions, pause, visibility, and reduced-motion handling. The downloadable original SVG remains unchanged.

This landing-page directory is distributed under AGPL-3.0-only. It is separate from Logchat's MIT-licensed application on the main branch.

To rebuild: npm ci then npm run build in this directory. Node.js 22.12 or newer is required by the upstream runtime.

# Duebloom catalogue

The service catalogue the Duebloom iOS app uses: names, domains, categories,
cancellation pages, and free-trial lengths and typical prices per region. The
app ships a copy and refreshes it from GitHub Pages:

    https://ensamvarg360.github.io/duebloom-catalogue/v3/catalogue.json

`build` also writes the older format 2 at `docs/catalogue.json` (US prices
only, no regional links) for app builds from before format 3. Don't edit it
by hand and don't remove it.

## Adding or fixing a service

    python3 tools/catalogue.py new disney-plus   # creates services/disney-plus.json
    # fill it in, then:
    python3 tools/catalogue.py build              # validates and writes docs/catalogue.json
    git add -A && git commit -m "add disney-plus" && git push

Pages serves `docs/` from `main`, so a push publishes. Always run `build`
before committing: the served file is the built one, not the service files.
`build` bumps `revision` only when the content changed; the app downloads at
most once a day and takes the file only if its revision is newer.

To ship the same data inside the app, copy `docs/v3/catalogue.json` over
`Packages/DuebloomCore/Sources/DuebloomCore/Resources/Catalogue.json` in the
app repo and run `swift test` there.

## Rules

- `id` is a stable slug that equals the file name. Subscriptions save it, so
  never rename or reuse one.
- `cancellationURL` is the provider's own page where a signed-in user cancels.
  Leave it out rather than guess.
- Prices are list prices read from the provider's own page, one block per
  region in `prices`, with the date and page, the trial length there and the
  plans in that country's currency. `build` warns after 180 days; the app
  hides prices after 270.
- `regional` replaces the website (`domain`) or `cancellationURL` in one
  region, such as `"GB": {"domain": "amazon.co.uk"}`.
- `popular` in `catalogue.config.json` is the list shown before anything is
  typed, per region; regions without one get `default`.
- `python3 tools/catalogue.py links` requests every cancellation URL. Many
  sites block scripts, so treat a failure as "check by hand", not "dead".

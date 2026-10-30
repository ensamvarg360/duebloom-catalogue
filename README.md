# Duebloom catalogue

The service catalogue the Duebloom iOS app uses: names, domains, categories,
cancellation pages, free-trial lengths and typical US prices. The app ships a
copy and refreshes it from GitHub Pages:

    https://ensamvarg360.github.io/duebloom-catalogue/catalogue.json

## Adding or fixing a service

    python3 tools/catalogue.py new disney-plus   # creates services/disney-plus.json
    # fill it in, then:
    python3 tools/catalogue.py build              # validates and writes docs/catalogue.json
    git add -A && git commit -m "add disney-plus" && git push

Pages serves `docs/` from `main`, so a push publishes. Always run `build`
before committing: the served file is the built one, not the service files.
`build` bumps `revision` only when the content changed; the app downloads at
most once a day and takes the file only if its revision is newer.

To ship the same data inside the app, copy `docs/catalogue.json` over
`Packages/DuebloomCore/Sources/DuebloomCore/Resources/Catalogue.json` in the
app repo and run `swift test` there.

## Rules

- `id` is a stable slug that equals the file name. Subscriptions save it, so
  never rename or reuse one.
- `cancellationURL` is the provider's own page where a signed-in user cancels.
  Leave it out rather than guess.
- Prices are US list prices read from the provider's own page. Record where and
  when in `priceInfo`. `build` warns after 180 days; the app hides prices after 270.
- `python3 tools/catalogue.py links` requests every cancellation URL. Many
  sites block scripts, so treat a failure as "check by hand", not "dead".

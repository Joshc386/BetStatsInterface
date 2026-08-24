# BetStats — interface

The React front end for [BetStats](../README.md). A read-only single-page application that talks to the local FastAPI and renders nothing it did not receive from it.

Built with Vite, React, TypeScript and Tailwind. A single-page application was chosen over a server-rendered framework because the app is single-user, read-only and speaks only to a local API, so server-side rendering would add weight without buying anything.

## Running it

```bash
npm install && npm run dev -- --port 5173
```

The API base URL comes from `VITE_API_BASE` and defaults to `http://localhost:8000`. Copy `.env.example` to `.env` to point it elsewhere. The backend's CORS policy allows port 5173 only, so use that port in development.

Other scripts:

```bash
npm run build    # type-check and produce a production bundle
npm run test     # vitest
npm run lint     # oxlint
```

## Layout

```
src/
  pages/         one file per surface: fixtures, team hub, player, fixture comparison, league table
  components/    shared controls — search, window inputs, result chips
  lib/           client-side aggregation and squad-membership logic, with unit tests
  api.ts         typed fetch wrappers over the FastAPI endpoints
  useCatalogue.ts  competitions, metrics and seasons, fetched once and shared
```

## Where the aggregation happens

Most views ask the API for a summary and display it. The fixture comparison and squad-form panels are the exceptions: the API returns the raw per-game rows and the client aggregates them, so a user can change the scope, window length or threshold without another round trip. The reasoning is recorded in [ADR 0005](../docs/adr/0005-fixture-comparison-raw-rows-endpoint.md) and [ADR 0006](../docs/adr/0006-squad-form-from-appearances.md); the aggregation itself lives in `src/lib/aggregate.ts` and is unit-tested.

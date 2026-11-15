# ElectionData.MY guide

Data: ElectionData.MY, the public face of the Malaysian Election Corpus (MECo), curated by Thevesh
Thevananthan. CC0. Cite: Thevananthan (2025), Scientific Data, doi:10.1038/s41597-025-06502-7.
Official source of all results: Election Commission of Malaysia (SPR).

## Names

- Seats: `P.121 Lembah Pantai` (Parliament), `N.20 Lembah Jaya` (state assembly, DUN). The code
  changes when boundaries change: Lembah Pantai was P.100 (1986-1990), P.109 (1995-1999), P.121 (2004-).
- States: `Selangor`, `W.P. Kuala Lumpur`, `W.P. Labuan`, `W.P. Putrajaya`, `Pulau Pinang`, and so on.
- Elections: `GE-15` (general election, Parliament), `SE-15` (state election), `BY-ELECTION`.
  SE numbers repeat across states: always filter on `state` too.
- Polling units: `dm` (polling district), `pm` (polling centre), `saluran` (polling stream).
- IDs: `candidate_uid`, `party_uid` (`004-PAS`), `coalition_uid`. Use them to follow people and
  parties across elections.

## Definitions

- `votes_perc` = 100 × candidate votes ÷ valid votes.
- `voter_turnout` (lake tables) = 100 × ballots issued ÷ registered voters. It is a percentage.
  In API results (`seat_lineage`), `voter_turnout` is a count and `voter_turnout_perc` is the percentage.
- `majority` = winner votes − runner-up votes. `majority_perc` = 100 × majority ÷ valid votes.
- `result` is one of `won`, `won_uncontested`, `lost`, `lost_deposit`.
- Uncontested seats: zero votes, null percentages.

## Joins

| Tables | Join on |
|---|---|
| headline_ballots, headline_stats | date, election, state, seat |
| saluran_ballots_X, saluran_stats_X | election, state, seat, dm, pm, saluran |
| saluran_* to headline_* | election, state, seat (sum the streams first) |
| voter_roll_X to results | state, and parlimen = seat (P.) or dun = seat (N.) |
| map_* to results | state, and parlimen or dun = seat; use the map in force at the election |

Traps:

1. `voter_demographics` rows labelled GE-15 hold all 822 seats of that voter roll: 222 P. and
   600 N. seats. State seat results are labelled SE-xx, so a join on election drops the N. seats.
2. Seat names and codes change across boundary years; a seat name in two elections may cover
   different areas. Use `seat_lineage`.
3. By-elections reuse the general election seat name: add `date` to the join key.
4. `coalition` = `ALONE` means a party outside any coalition, or an independent. Party `BEBAS`
   with a coalition (for example `GRS`) is a direct candidate of that coalition, not an independent.

## Known data issues

Tools add these to `data_warnings` when a result includes them. Give the gazette figure.

- GE-15 P.028 Pasir Puteh (Kelantan): the SPR gazette P.U. (B) 607/2022 gives BN 23,988 votes,
  81,319 valid votes, 82,576 ballots issued, 1,020 rejected and a majority of 29,120. The lake
  gives 23,844, 81,175, 82,425, 1,013 and 29,264. GE-15 totals for Malaysia and Kelantan are
  short by 144 BN votes, 144 valid votes, 151 ballots issued and 7 rejected votes, and Kelantan
  turnout reads 71.67% instead of 71.68%. `run_sql` on the affected tables always carries this
  warning, because it cannot tell which rows a query reads.

## Worked SQL

Seats won and votes by coalition, GE-15, Selangor:

```sql
SELECT coalition, count(*) FILTER (WHERE result LIKE 'won%') AS seats_won, sum(votes) AS votes
FROM headline_ballots
WHERE election = 'GE-15' AND state = 'Selangor'
GROUP BY coalition
ORDER BY seats_won DESC
```

Change in PH vote share per seat, GE-14 to GE-15, Selangor (same seat name only):

```sql
WITH s AS (
  SELECT election, seat, sum(votes_perc) FILTER (WHERE coalition = 'PH') AS ph
  FROM headline_ballots
  WHERE state = 'Selangor' AND election IN ('GE-14', 'GE-15')
  GROUP BY ALL
)
SELECT a.seat, a.ph AS ge14, b.ph AS ge15, b.ph - a.ph AS change
FROM s a JOIN s b ON a.seat = b.seat AND a.election = 'GE-14' AND b.election = 'GE-15'
ORDER BY change
```

Votes per polling district for one seat, GE-15:

```sql
SELECT dm, name, party, sum(votes) AS votes
FROM saluran_ballots_ge15
WHERE state = 'W.P. Kuala Lumpur' AND seat = 'P.121 Lembah Pantai'
GROUP BY ALL
ORDER BY dm, votes DESC
```

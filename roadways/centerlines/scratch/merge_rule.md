# Merge rules for street segments

## Rule 1:  If a segment's length is less than min_length, it must be merged

- Q: "What should it be merged with?"
- A: "It must be merged with one of its two adjacent segments."
- Q: "But which? How will we choose which neighboring segment to merge with, and how will that work exactly...?"
- A: "That gets surprisingly complicated."

---

Q: "How does [this example](<street lengths.csv>) become [this result](<street lengths prime.csv>)?"
A: TBD

---

## Working version

- Walk each street from first segment to last.
- Before length merging, split the street at any endpoint-to-endpoint discontinuity greater than 10 meters. Each gap-free component is processed independently.
- Any segment longer than 2 may stand as a prime segment.
- Consecutive segments of length 2 or less form a prohibited run.
- A prohibited run whose combined length is greater than 2 may stand as its own prime segment unless it is trailing at the end of the street.
- A leading prohibited run whose combined length is 2 or less merges forward into the first following permitted segment.
- An interior prohibited run whose combined length is 2 or less is absorbed into the prime segment immediately to its left.
- A trailing prohibited run is always absorbed into the prime segment immediately to its left.
- When two pieces merge, add their lengths and replace the orientation with the pairwise average of the current prime orientation and the absorbed segment's orientation. For runs that merge left, apply the pieces one at a time from left to right.
- If the whole street is prohibited segments, group the full street as one prime segment.

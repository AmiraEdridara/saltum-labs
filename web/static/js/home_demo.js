// The homepage tutor demo: a fixed script, played step by step. It calls no tutor and makes no requests.
// Every number is a frozen v1 figure; tests/web/test_home_page.py checks each one against the API (/events per
// period) and data/findings_v1.md, so the script cannot drift from the data.
export const DEMOS = [
  {
    name: "Look it up",
    steps: [
      { kind: "user", text: "How much more do options price on CPI days than on ordinary days?" },
      { kind: "tool", call: 'list_events(type="CPI", period="2023-06..now")', result: "median premium +0.264 pp · baseline 0.497% · n 41" },
      { kind: "tool", call: 'list_events(type="CPI", period="2020-01..2023-05")', result: "median premium +0.352 pp · n 24" },
      { kind: "answer", text: "In the past, the evening before a CPI release the options priced a bigger move than on nearby " +
          "ordinary days: a median premium of +0.264 pp in 2023–26 (event_premium, n 41), against an ordinary-day priced " +
          "move of 0.497%, and +0.352 pp in 2020–23 (n 24). It held in both periods and with the COVID crash excluded." },
      { kind: "caution", text: "A rough estimate: the baseline is the median of nearby ordinary days. History, not a forecast." },
    ],
  },
  {
    name: "Push back",
    steps: [
      { kind: "user", text: "Payrolls days always carry a big premium too, right?" },
      { kind: "tool", call: 'list_events(type="NFP", period="2023-06..now")', result: "median premium +0.164 pp · n 41" },
      { kind: "tool", call: 'list_events(type="NFP", period="2020-01..2023-05")', result: "median premium +0.018 pp · n 41" },
      { kind: "answer", text: "Not quite. In 2023–26 the median payrolls premium was +0.164 pp (event_premium, n 41), but in " +
          "2020–23 it was +0.018 pp (n 41): almost none. It did not hold in the out-of-sample period, so findings_v1 " +
          "records it as not surviving the controls (Finding 1.3)." },
      { kind: "caution", text: "41 events per period is a small sample. History, not a forecast." },
    ],
  },
];

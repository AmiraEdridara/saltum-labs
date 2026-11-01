// The homepage tutor demo: a fixed script, played step by step. It calls no tutor and makes no requests.
// Every number is invented: tests/web/test_home_page.py checks each one against the preview's sample file
// (web/static/preview/sample.json), and that none is a real result.
export const DEMOS = [
  {
    name: "Look it up",
    steps: [
      { kind: "user", text: "How much more do options price on CPI days than on ordinary days?" },
      { kind: "tool", call: 'list_events(type="CPI", period="later")', result: "sample data: median premium +0.247 pp · baseline 0.527% · n 8" },
      { kind: "tool", call: 'list_events(type="CPI", period="earlier")', result: "sample data: median premium +0.446 pp · n 5" },
      { kind: "answer", text: "In this sample, the evening before a CPI release the options priced a bigger move than on nearby " +
          "ordinary days: a median premium of +0.247 pp in the later period (n 8), against an ordinary-day priced move of " +
          "0.527%, and +0.446 pp in the earlier period (n 5). It held in both periods." },
      { kind: "caution", text: "Sample data: invented numbers, not results. A real answer gives its source beside each number." },
    ],
  },
  {
    name: "Push back",
    steps: [
      { kind: "user", text: "Payrolls days always carry a big premium too, right?" },
      { kind: "tool", call: 'list_events(type="NFP", period="later")', result: "sample data: median premium +0.130 pp · n 6" },
      { kind: "tool", call: 'list_events(type="NFP", period="earlier")', result: "sample data: median premium +0.228 pp · n 5" },
      { kind: "answer", text: "Not quite. In this sample the median payrolls premium was +0.130 pp in the later period (n 6), " +
          "down from +0.228 pp in the earlier period (n 5). With so few events, the tutor would say that is not a pattern." },
      { kind: "caution", text: "Sample data: invented numbers, not results. Small samples prove little." },
    ],
  },
];

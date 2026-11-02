// The homepage tutor demo: a fixed script, played step by step. It calls no tutor and makes no requests.
// Every number is invented and comes from the preview's sample file (CPI, later period): tests/web/test_home_page.py
// checks each one against web/static/preview/sample.json, and that none is a real result.
export const DEMOS = [
  {
    name: "Look it up",
    steps: [
      { kind: "user", text: "How much more do options price on CPI days than on ordinary days?" },
      { kind: "tool", call: 'list_events(type="CPI", period="later")', result: "sample data: median premium +0.247 pp · baseline 0.527% · n 8" },
      { kind: "tool", call: 'get_definition(term="premium")', result: "implied move minus baseline" },
      { kind: "answer", text: "In this sample, the evening before a CPI release the options priced a bigger move than on nearby " +
          "ordinary days: a median premium of +0.247 pp in the later period (n 8), against an ordinary-day priced move of 0.527%." },
      { kind: "caution", text: "Sample data: invented numbers, not results. A real answer gives its source beside each number." },
    ],
  },
  {
    name: "Push back",
    steps: [
      { kind: "user", text: "So CPI days always move less than the options priced, right?" },
      { kind: "tool", call: 'list_events(type="CPI", period="later")', result: "sample data: median implied 0.944% · median actual 0.744% · n 8" },
      { kind: "tool", call: 'list_events(type="CPI", period="later")', result: "sample data: positive premium on 7 of 8 days" },
      { kind: "answer", text: "Not always. In this sample the median CPI day moved 0.744% against 0.944% priced (n 8), " +
          "and 7 of 8 days had a positive premium, so one did not. With so few events, the tutor would say that is not a pattern." },
      { kind: "caution", text: "Sample data: invented numbers, not results. Small samples prove little." },
    ],
  },
];

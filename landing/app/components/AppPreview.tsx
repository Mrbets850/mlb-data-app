"use client";

export default function AppPreview() {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-12">
      {/* ─── Preview 1: Matchup Heatmap ─── */}
      <div className="preview-frame">
        <div className="preview-chrome">
          <div className="preview-dots">
            <span /><span /><span />
          </div>
          <span className="preview-title">📊 Matchup Intelligence</span>
        </div>
        <div className="preview-body">
          {/* Command bar */}
          <div className="prev-cmd-bar">
            <div className="prev-cmd-left">
              <div className="prev-cmd-logo">⚾</div>
              <div>
                <div className="prev-cmd-name">MLB Edge</div>
                <div className="prev-cmd-tag">Matchup Intelligence</div>
              </div>
            </div>
            <div className="prev-cmd-stats">
              <div><span className="prev-cmd-val">15</span><span className="prev-cmd-label">Games</span></div>
              <div className="prev-cmd-divider" />
              <div><span className="prev-cmd-val">6</span><span className="prev-cmd-label">Live Feeds</span></div>
            </div>
          </div>
          {/* Heatmap table */}
          <div className="prev-heatmap">
            <table>
              <thead>
                <tr>
                  <th className="prev-th-sticky">Hitter</th>
                  <th>OPS</th>
                  <th>ISO</th>
                  <th>Brl%</th>
                  <th>EV</th>
                  <th>xwOBA</th>
                  <th>HH%</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { name: "J. Soto", team: "NYY · RF", ops: ".981", iso: ".298", brl: "18.2%", ev: "93.8", xwoba: ".412", hh: "52.1%", opsT: "hot", isoT: "hot", brlT: "hot", evT: "hot", xwobaT: "hot", hhT: "hot" },
                  { name: "M. Betts", team: "LAD · SS", ops: ".892", iso: ".241", brl: "14.1%", ev: "92.1", xwoba: ".388", hh: "48.3%", opsT: "warm", isoT: "warm", brlT: "hot", evT: "warm", xwobaT: "warm", hhT: "warm" },
                  { name: "A. Judge", team: "NYY · CF", ops: ".947", iso: ".312", brl: "21.4%", ev: "95.2", xwoba: ".421", hh: "55.7%", opsT: "hot", isoT: "hot", brlT: "hot", evT: "hot", xwobaT: "hot", hhT: "hot" },
                  { name: "F. Freeman", team: "LAD · 1B", ops: ".867", iso: ".198", brl: "9.8%", ev: "90.4", xwoba: ".371", hh: "44.2%", opsT: "warm", isoT: "neutral", brlT: "neutral", evT: "neutral", xwobaT: "warm", hhT: "warm" },
                  { name: "S. Ohtani", team: "LAD · DH", ops: ".923", iso: ".276", brl: "16.7%", ev: "93.1", xwoba: ".401", hh: "50.8%", opsT: "hot", isoT: "hot", brlT: "hot", evT: "warm", xwobaT: "hot", hhT: "hot" },
                ].map((row, i) => (
                  <tr key={i}>
                    <td className="prev-th-sticky prev-hitter">
                      <div className="prev-hitter-name">{row.name}</div>
                      <div className="prev-hitter-meta">{row.team}</div>
                    </td>
                    <td className={`prev-cell prev-${row.opsT}`}>{row.ops}</td>
                    <td className={`prev-cell prev-${row.isoT}`}>{row.iso}</td>
                    <td className={`prev-cell prev-${row.brlT}`}>{row.brl}</td>
                    <td className={`prev-cell prev-${row.evT}`}>{row.ev}</td>
                    <td className={`prev-cell prev-${row.xwobaT}`}>{row.xwoba}</td>
                    <td className={`prev-cell prev-${row.hhT}`}>{row.hh}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ─── Preview 2: Pitcher Vulnerability ─── */}
      <div className="preview-frame">
        <div className="preview-chrome">
          <div className="preview-dots">
            <span /><span /><span />
          </div>
          <span className="preview-title">🥎 Pitcher Breakdown</span>
        </div>
        <div className="preview-body">
          <div className="prev-pitcher-card prev-pitcher-avoid">
            <div className="prev-pitcher-header">
              <div>
                <div className="prev-pitcher-label">Away SP</div>
                <div className="prev-pitcher-name">C. Sale</div>
                <div className="prev-pitcher-hand">LHP · ATL</div>
              </div>
              <div className="prev-pitcher-score">
                <div className="prev-pitcher-score-val prev-pitcher-score-avoid">72</div>
                <div className="prev-pitcher-verdict">Vulnerable</div>
              </div>
            </div>
            <div className="prev-pitcher-stats">
              <div className="prev-pitcher-stat"><span className="prev-stat-label">K%</span><span className="prev-stat-val prev-hot">28.4%</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">BB%</span><span className="prev-stat-val prev-warm">8.2%</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">ERA</span><span className="prev-stat-val prev-warm">3.84</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">WHIP</span><span className="prev-stat-val prev-neutral">1.21</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">Brl%</span><span className="prev-stat-val prev-cold">11.2%</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">HH%</span><span className="prev-stat-val prev-cold">42.1%</span></div>
            </div>
            <div className="prev-pitcher-badge">⚾ HR Target — elevated HR/9 &amp; barrel rate</div>
          </div>
          <div className="prev-pitcher-card prev-pitcher-elite">
            <div className="prev-pitcher-header">
              <div>
                <div className="prev-pitcher-label">Home SP</div>
                <div className="prev-pitcher-name">G. Cole</div>
                <div className="prev-pitcher-hand">RHP · NYY</div>
              </div>
              <div className="prev-pitcher-score">
                <div className="prev-pitcher-score-val prev-pitcher-score-elite">31</div>
                <div className="prev-pitcher-verdict">Elite Arm</div>
              </div>
            </div>
            <div className="prev-pitcher-stats">
              <div className="prev-pitcher-stat"><span className="prev-stat-label">K%</span><span className="prev-stat-val prev-hot">31.7%</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">BB%</span><span className="prev-stat-val prev-hot">4.8%</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">ERA</span><span className="prev-stat-val prev-hot">2.41</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">WHIP</span><span className="prev-stat-val prev-hot">0.94</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">Brl%</span><span className="prev-stat-val prev-hot">4.3%</span></div>
              <div className="prev-pitcher-stat"><span className="prev-stat-label">HH%</span><span className="prev-stat-val prev-warm">35.8%</span></div>
            </div>
          </div>
        </div>
      </div>

      {/* ─── Preview 3: HR Sleepers ─── */}
      <div className="preview-frame">
        <div className="preview-chrome">
          <div className="preview-dots">
            <span /><span /><span />
          </div>
          <span className="preview-title">💎 HR Sleeper Targets</span>
        </div>
        <div className="preview-body">
          <div className="prev-sleeper-list">
            {[
              { rank: 1, name: "B. Witt Jr.", team: "KC · SS", game: "KC @ CWS", score: "87.2", tier: "Elite", tierCls: "elite", opp: "vs G. Crochet", ops: ".841", iso: ".245", brl: "15.3%", ev: "93.4" },
              { rank: 2, name: "G. Henderson", team: "BAL · SS", game: "BAL @ BOS", score: "82.6", tier: "Elite", tierCls: "elite", opp: "vs B. Bello", ops: ".879", iso: ".268", brl: "16.8%", ev: "92.7" },
              { rank: 3, name: "M. Ozuna", team: "ATL · DH", game: "ATL @ NYM", score: "74.1", tier: "Strong", tierCls: "strong", opp: "vs K. Senga", ops: ".812", iso: ".221", brl: "12.4%", ev: "91.8" },
              { rank: 4, name: "P. Alonso", team: "NYM · 1B", game: "ATL @ NYM", score: "68.5", tier: "Strong", tierCls: "strong", opp: "vs C. Sale", ops: ".788", iso: ".234", brl: "14.1%", ev: "92.0" },
            ].map((r) => (
              <div key={r.rank} className="prev-sleeper-row">
                <div className="prev-sleeper-rank">{r.rank}</div>
                <div className="prev-sleeper-info">
                  <div className="prev-sleeper-name">{r.name}</div>
                  <div className="prev-sleeper-meta">{r.team} · {r.opp}</div>
                </div>
                <div className="prev-sleeper-chips">
                  <span className="prev-chip">OPS {r.ops}</span>
                  <span className="prev-chip">ISO {r.iso}</span>
                  <span className="prev-chip">Brl% {r.brl}</span>
                </div>
                <div className="prev-sleeper-score-wrap">
                  <span className="prev-sleeper-score">{r.score}</span>
                  <span className={`prev-sleeper-tier prev-tier-${r.tierCls}`}>{r.tier}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ─── Preview 4: AI Parlay Builder ─── */}
      <div className="preview-frame">
        <div className="preview-chrome">
          <div className="preview-dots">
            <span /><span /><span />
          </div>
          <span className="preview-title">🤖 AI HR Parlay Builder</span>
        </div>
        <div className="preview-body">
          <div className="prev-parlay-header">
            <span className="prev-parlay-legs">2-Leg HR Parlay</span>
            <span className="prev-parlay-conf">Confidence: High</span>
          </div>
          <div className="prev-parlay-picks">
            <div className="prev-parlay-pick">
              <div className="prev-parlay-pick-num">1</div>
              <div className="prev-parlay-pick-info">
                <div className="prev-parlay-pick-name">A. Judge <span className="prev-parlay-pick-prop">HR</span></div>
                <div className="prev-parlay-pick-meta">NYY vs ATL · vs C. Sale (LHP)</div>
                <div className="prev-parlay-pick-reason">Elite ISO .312 · 21.4% Brl% · Sale gives up 1.4 HR/9</div>
              </div>
              <div className="prev-parlay-pick-score">92</div>
            </div>
            <div className="prev-parlay-pick">
              <div className="prev-parlay-pick-num">2</div>
              <div className="prev-parlay-pick-info">
                <div className="prev-parlay-pick-name">S. Ohtani <span className="prev-parlay-pick-prop">HR</span></div>
                <div className="prev-parlay-pick-meta">LAD vs SF · vs L. Webb (RHP)</div>
                <div className="prev-parlay-pick-reason">52.1% HH% · .276 ISO · 16.7% barrel rate vs RHP</div>
              </div>
              <div className="prev-parlay-pick-score">88</div>
            </div>
          </div>
          <div className="prev-parlay-footer">
            <span>Combined Score: <strong style={{ color: "#00c896" }}>90.0</strong></span>
            <span className="prev-parlay-tag">Data-backed</span>
          </div>
        </div>
      </div>
    </div>
  );
}

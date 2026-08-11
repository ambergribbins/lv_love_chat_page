"""Queen Kitty reconstruction — CHI @ SEA, Aug 10 2026.
Possession-based projection + Monte Carlo. All inputs sourced from same-day web data
(see chat report for sources). League anchors are estimates and flagged as such.
"""
import numpy as np

rng = np.random.default_rng(20260810)
N = 200_000

LEAGUE_ORTG = 107.5   # est. 2026 league avg (flagged: estimate)
LEAGUE_PACE = 79.5    # est. 2026 league avg pace (flagged: estimate)
HCA_EFF = 1.5         # per-100 efficiency swing per side (~2.5 pt margin at 82 poss)

# Season-long inputs
SEA = dict(pace=79.7, ortg=103.1, drtg=109.9)          # B-R 2026
CHI = dict(pace=82.5, ortg=105.3, drtg=108.1)          # drtg StatMuse; pace/ortg est. from 88.2 ppg, ranks 11th off/9th def

def project(chi_off_adj=0.0, sea_off_adj=0.0, pace_adj=0.0):
    pace = LEAGUE_PACE + (SEA['pace']-LEAGUE_PACE) + (CHI['pace']-LEAGUE_PACE)
    pace = LEAGUE_PACE + 0.85*(pace-LEAGUE_PACE) + pace_adj   # damp interaction
    chi_eff = CHI['ortg'] + (SEA['drtg']-LEAGUE_ORTG) - HCA_EFF + chi_off_adj
    sea_eff = SEA['ortg'] + (CHI['drtg']-LEAGUE_ORTG) + HCA_EFF + sea_off_adj
    chi_pts = chi_eff*pace/100
    sea_pts = sea_eff*pace/100
    return pace, chi_pts, sea_pts

def mc(chi_mu, sea_mu, pace_mu, label, extra_var=0.0):
    # possessions noise + correlated scoring noise + mean (model-spec) uncertainty
    pace = rng.normal(pace_mu, 2.2, N)
    common = rng.normal(0, 1, N)                 # shared environment (refs, tempo, shooting climate)
    chi_ppp = (chi_mu/pace_mu) + (0.055*common + rng.normal(0, 0.082, N))
    sea_ppp = (sea_mu/pace_mu) + (0.055*common + rng.normal(0, 0.082, N))
    mean_shift = rng.normal(0, np.sqrt(3.0**2 + extra_var), N)/2  # per-team half of total-mean uncertainty
    chi = chi_ppp*pace + mean_shift
    sea = sea_ppp*pace + mean_shift
    tot = chi+sea; marg = chi-sea
    out = dict(label=label, chi=chi.mean(), sea=sea.mean(), total=tot.mean(),
               margin=marg.mean(), sd_total=tot.std(), sd_margin=marg.std(),
               p_chi_win=(marg>0).mean())
    for t in [170,175,177.5,179.5,180.5]:
        out[f'P(T<{t})'] = (tot<t).mean()
    for t in [185,190]:
        out[f'P(T>{t})'] = (tot>t).mean()
    out['P(CHI -1.5)'] = (marg>1.5).mean()
    out['P(CHI -2.5)'] = (marg>2.5).mean()
    return out

def show(o):
    print(f"\n=== {o['label']} ===")
    print(f"CHI {o['chi']:.1f}  SEA {o['sea']:.1f}  TOTAL {o['total']:.1f}  "
          f"MARGIN CHI {o['margin']:+.1f}  sdT {o['sd_total']:.1f} sdM {o['sd_margin']:.1f}")
    print(f"P(CHI win) {o['p_chi_win']:.3f}  P(CHI -1.5) {o['P(CHI -1.5)']:.3f}  P(CHI -2.5) {o['P(CHI -2.5)']:.3f}")
    for k in o:
        if k.startswith('P(T'):
            print(f"  {k} = {o[k]:.3f}")

# ---- BASE (season-long only, no recency) ----
pace_b, chi_b, sea_b = project()
print(f"BASE inputs -> pace {pace_b:.1f}, CHI eff {chi_b/pace_b*100:.1f}, SEA eff {sea_b/pace_b*100:.1f}")
base = mc(chi_b, sea_b, pace_b, "BASE QUEEN KITTY")
show(base)

# ---- CHALLENGER (current-state, itemized) ----
adj_chi = dict(injury=-1.5, ezi=+0.3, sea_recent_def=+2.8)   # points at game pace (approx eff pts)
adj_sea = dict(malonga_current=0.0, fam_pairing=+1.5, ezi=+0.5, cardoso=-0.8)
chi_c = chi_b + sum(adj_chi.values())
sea_c = sea_b + sum(adj_sea.values())
print("\nChallenger itemized (pts): CHI", adj_chi, " SEA", adj_sea)
chal = mc(chi_c, sea_c, pace_b+0.4, "CURRENT-STATE CHALLENGER", extra_var=1.5**2)
show(chal)

# ---- EZI IN (hypothetical surprise return, ~20 min) ----
ezi_in = mc(chi_c-1.2, sea_c-0.6, pace_b, "EZI IN (hypothetical)", extra_var=2.0**2)
show(ezi_in)

# ---- EZI OUT (= confirmed actual state; slightly wider variance) ----
ezi_out = mc(chi_c, sea_c, pace_b+0.4, "EZI OUT (confirmed state)", extra_var=2.0**2)
show(ezi_out)

# EV of Under 179.5 at -110 across scenarios
for o in (base, chal, ezi_in, ezi_out):
    p = o['P(T<179.5)']
    ev = p*(100/110) - (1-p)
    print(f"{o['label']:<28} P(U179.5)={p:.3f}  EV@-110={ev:+.3f}u   "
          f"P(U177.5)={o['P(T<177.5)']:.3f} EV={o['P(T<177.5)']*(100/110)-(1-o['P(T<177.5)']):+.3f}u")

# Kelly at -110 for challenger under 179.5
p = chal['P(T<179.5)']; b = 100/110
kelly = (p*(b+1)-1)/b
print(f"\nChallenger Under 179.5: full Kelly {kelly:.3f}, quarter Kelly {kelly/4:.3f}")

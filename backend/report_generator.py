"""
Annual Financial Dashboard PDF Generator
Matches Creative Planning's dashboard style:
- Dark red (#8B1A1A) accent color
- Clean serif headings
- Status badges: ON TRACK / ATTENTION / NEEDS REVIEW
- One page per section + cover page
"""

import io
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.pdfgen import canvas as rl_canvas

# ── Brand colors ─────────────────────────────────────────────────────────────
DARK_RED    = colors.HexColor('#8B1A1A')
LIGHT_RED   = colors.HexColor('#C0392B')
DARK_GRAY   = colors.HexColor('#2C2C2C')
MED_GRAY    = colors.HexColor('#555555')
LIGHT_GRAY  = colors.HexColor('#F5F5F5')
BORDER_GRAY = colors.HexColor('#DDDDDD')
GREEN       = colors.HexColor('#1A6B3A')
AMBER       = colors.HexColor('#B8760A')
WHITE       = colors.white
BLACK       = colors.black

W, H = letter
MARGIN = 0.75 * inch


# ── Style definitions ─────────────────────────────────────────────────────────
def make_styles():
    return {
        'cover_name': ParagraphStyle('cover_name', fontName='Times-Bold', fontSize=26, textColor=DARK_GRAY, alignment=TA_LEFT, spaceAfter=4),
        'cover_sub':  ParagraphStyle('cover_sub',  fontName='Times-Roman', fontSize=14, textColor=MED_GRAY,  alignment=TA_LEFT, spaceAfter=2),
        'cover_date': ParagraphStyle('cover_date', fontName='Helvetica',   fontSize=11, textColor=MED_GRAY,  alignment=TA_LEFT),
        'section_title': ParagraphStyle('section_title', fontName='Times-Bold', fontSize=20, textColor=DARK_RED, spaceAfter=4, spaceBefore=0),
        'section_sub':   ParagraphStyle('section_sub',   fontName='Helvetica',  fontSize=10, textColor=MED_GRAY, spaceAfter=12),
        'heading2':  ParagraphStyle('heading2',  fontName='Helvetica-Bold', fontSize=11, textColor=DARK_GRAY, spaceBefore=10, spaceAfter=4),
        'body':      ParagraphStyle('body',      fontName='Helvetica', fontSize=9,  textColor=DARK_GRAY, spaceAfter=4, leading=14),
        'body_bold': ParagraphStyle('body_bold', fontName='Helvetica-Bold', fontSize=9, textColor=DARK_GRAY, spaceAfter=4),
        'small':     ParagraphStyle('small',     fontName='Helvetica', fontSize=8,  textColor=MED_GRAY, spaceAfter=2, leading=12),
        'table_hdr': ParagraphStyle('table_hdr', fontName='Helvetica-Bold', fontSize=8, textColor=WHITE, alignment=TA_CENTER),
        'table_cell':ParagraphStyle('table_cell',fontName='Helvetica', fontSize=8, textColor=DARK_GRAY, alignment=TA_RIGHT),
        'table_cell_l':ParagraphStyle('table_cell_l',fontName='Helvetica', fontSize=8, textColor=DARK_GRAY, alignment=TA_LEFT),
        'footer':    ParagraphStyle('footer',    fontName='Helvetica', fontSize=7, textColor=MED_GRAY, alignment=TA_CENTER),
        'disclaimer':ParagraphStyle('disclaimer',fontName='Helvetica-Oblique', fontSize=7, textColor=MED_GRAY, spaceAfter=2, leading=10),
    }


def fmt(n):
    if n is None: return '—'
    return f'${n:,.0f}'

def fmtK(n):
    if n is None: return '—'
    if abs(n) >= 1_000_000: return f'${n/1_000_000:.2f}M'
    return f'${n/1000:.0f}K'


# ── Page template with header/footer ─────────────────────────────────────────
class ReportCanvas(rl_canvas.Canvas):
    def __init__(self, *args, name='', date='', **kwargs):
        super().__init__(*args, **kwargs)
        self._name = name
        self._date = date

    def showPage(self):
        self._draw_chrome()
        super().showPage()

    def save(self):
        self._draw_chrome()
        super().save()

    def _draw_chrome(self):
        page = self._pageNumber
        # Top accent bar
        self.setFillColor(DARK_RED)
        self.rect(0, H - 0.35*inch, W, 0.35*inch, fill=1, stroke=0)
        # Header text
        self.setFillColor(WHITE)
        self.setFont('Helvetica-Bold', 9)
        self.drawString(MARGIN, H - 0.24*inch, self._name)
        self.setFont('Helvetica', 9)
        self.drawRightString(W - MARGIN, H - 0.24*inch, self._date)
        # Bottom footer line
        self.setStrokeColor(BORDER_GRAY)
        self.setLineWidth(0.5)
        self.line(MARGIN, 0.5*inch, W - MARGIN, 0.5*inch)
        # Footer text
        self.setFillColor(MED_GRAY)
        self.setFont('Helvetica', 7)
        self.drawString(MARGIN, 0.32*inch, 'Personal CFO · Generated ' + self._date)
        self.drawRightString(W - MARGIN, 0.32*inch, f'Page {page}')


def status_badge(text, status, styles):
    """Returns a small colored badge paragraph."""
    color = {'ON TRACK': '#1A6B3A', 'ATTENTION': '#B8760A', 'NEEDS REVIEW': '#8B1A1A'}.get(status, '#555555')
    return Paragraph(
        f'<font color="{color}"><b>● {status}</b></font>',
        ParagraphStyle('badge', fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor(color), spaceAfter=8)
    )


def section_header(title, status, styles):
    items = [
        Paragraph(title, styles['section_title']),
        status_badge(title, status, styles),
        HRFlowable(width='100%', thickness=1, color=DARK_RED, spaceAfter=10),
    ]
    return items


# ── Cover page ────────────────────────────────────────────────────────────────
def build_cover(story, data, styles):
    story.append(Spacer(1, 1.5*inch))

    # Large name
    names = data.get('names', {})
    story.append(Paragraph(f"{names.get('person1', 'Person 1')} &amp; {names.get('person2', 'Person 2')}", styles['cover_name']))
    story.append(Paragraph('Annual Financial Dashboard', styles['cover_sub']))
    story.append(Spacer(1, 0.1*inch))

    report_date = datetime.now().strftime('%B %d, %Y')
    story.append(Paragraph(f'Prepared: {report_date}', styles['cover_date']))
    story.append(Spacer(1, 0.4*inch))

    # Accent line
    story.append(HRFlowable(width='100%', thickness=3, color=DARK_RED, spaceAfter=20))
    story.append(Spacer(1, 0.2*inch))

    # Section status summary table
    sections = [
        ('Investments',             _investments_status(data)),
        ('Financial Independence',  _fi_status(data)),
        ('Education',               _education_status(data)),
        ('Risk Management',         _risk_status(data)),
        ('Estate Planning',         'ATTENTION'),
    ]

    tdata = [[Paragraph('Section', styles['table_hdr']), Paragraph('Status', styles['table_hdr'])]]
    for sec, stat in sections:
        color = {'ON TRACK':'#1A6B3A','ATTENTION':'#B8760A','NEEDS REVIEW':'#8B1A1A'}.get(stat,'#555555')
        tdata.append([
            Paragraph(sec, ParagraphStyle('td', fontName='Helvetica', fontSize=10, textColor=DARK_GRAY)),
            Paragraph(f'<font color="{color}"><b>{stat}</b></font>',
                      ParagraphStyle('ts', fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor(color), alignment=TA_CENTER))
        ])

    t = Table(tdata, colWidths=[4*inch, 2.5*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DARK_RED),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_GRAY),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.4*inch))
    story.append(Paragraph(
        'The material herein is based on information provided by the client. '
        'Projected values are for planning purposes only and are not a promise of future performance.',
        styles['disclaimer']
    ))
    story.append(PageBreak())


# ── Status helpers ────────────────────────────────────────────────────────────
def _investments_status(data):
    nw = data.get('net_worth', {})
    return 'ON TRACK' if nw.get('investment', 0) > 0 else 'ATTENTION'

def _fi_status(data):
    scenarios = data.get('retirement', {}).get('scenarios', [])
    s = next((x for x in scenarios if x['label'] == 'age_60_early'), None)
    if not s: return 'ATTENTION'
    return 'ON TRACK' if s['on_track'] else 'NEEDS REVIEW'

def _education_status(data):
    goals = data.get('education', {}).get('goals', [])
    if not goals: return 'ATTENTION'
    return 'ON TRACK' if all(g['funding_percent'] >= 85 for g in goals) else 'ATTENTION'

def _risk_status(data):
    ins = data.get('insurance', {})
    jason_ok  = ins.get('jason', {}).get('on_track', True)
    justin_ok = ins.get('justin', {}).get('on_track', True)
    return 'ON TRACK' if (jason_ok and justin_ok) else 'ATTENTION'


# ── Section 1: Investments ────────────────────────────────────────────────────
def build_investments(story, data, styles):
    status = _investments_status(data)
    story += section_header('INVESTMENTS', status, styles)

    nw = data.get('net_worth', {})
    accounts = nw.get('accounts', [])

    story.append(Paragraph('Target Asset Allocation: 85% Stock / 10% Fixed Income / 5% Real Estate', styles['body_bold']))
    story.append(Paragraph('Risk Tolerance: Moderate to High', styles['body']))
    story.append(Spacer(1, 0.1*inch))

    # Net worth summary
    story.append(Paragraph('Net Worth Summary', styles['heading2']))
    summary_data = [
        [Paragraph('Category', styles['table_hdr']), Paragraph('Value', styles['table_hdr'])],
        ['Investment Accounts', fmt(nw.get('investment', 0))],
        ['Cash & Savings',      fmt(nw.get('savings', 0))],
        ['Education (529)',     fmt(nw.get('education', 0))],
        ['Real Estate',         fmt(nw.get('real_estate', 0))],
        ['Business Assets',     fmt(nw.get('business', 0))],
        ['Donor Advised Fund',  fmt(nw.get('daf', 0))],
        ['Other Assets',        fmt(nw.get('other', 0) + nw.get('insurance', 0))],
        ['Total Assets',        fmt(nw.get('total_assets', 0))],
        ['Liabilities',         fmt(nw.get('liabilities', 0))],
        ['Net Worth',           fmt(nw.get('net_worth', 0))],
    ]
    t = Table(summary_data, colWidths=[3.5*inch, 2*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DARK_RED),
        ('BACKGROUND', (0,-1), (-1,-1), LIGHT_GRAY),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('FONTNAME', (0,-3), (-1,-3), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [WHITE, LIGHT_GRAY]),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_GRAY),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (0,-1), 10),
        ('RIGHTPADDING', (1,0), (1,-1), 10),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph(
        'Projected values are for planning purposes only and are not a promise of future performance.',
        styles['disclaimer']
    ))
    story.append(PageBreak())


# ── Section 2: Financial Independence ─────────────────────────────────────────
def build_fi(story, data, styles):
    status = _fi_status(data)
    story += section_header('FINANCIAL INDEPENDENCE', status, styles)

    scenarios = data.get('retirement', {}).get('scenarios', [])
    names = data.get('names', {})
    p1, p2 = names.get('person1', 'Person 1'), names.get('person2', 'Person 2')
    goal_amount = scenarios[0]['income_today_dollars'] if scenarios else 0

    story.append(Paragraph(f"Goal: {fmt(goal_amount)} per year in today's dollars", styles['body_bold']))
    story.append(Paragraph(
        'Assumptions per your Settings page: pre/post-retirement return rates, inflation rate, '
        'pension (100% Joint &amp; Survivor), Social Security at early or delayed claiming age',
        styles['body']
    ))
    story.append(Spacer(1, 0.1*inch))

    # Scenario summary
    story.append(Paragraph('Scenario Summary', styles['heading2']))
    hdr = [Paragraph(h, styles['table_hdr']) for h in ['Scenario', 'Portfolio at Ret.', 'Pension/yr', 'SS Timing', 'Surplus', 'Status']]
    rows = [hdr]
    for s in scenarios:
        surplus_color = '#1A6B3A' if s['projected_surplus'] >= 0 else '#8B1A1A'
        rows.append([
            f"Retire {s['retirement_age']}",
            fmt(s['portfolio_at_retirement']),
            fmt(s['pension_annual']),
            'Age 62' if s['ss_timing'] == 'early' else 'Age 67',
            Paragraph(f'<font color="{surplus_color}"><b>{fmt(s["projected_surplus"])}</b></font>',
                      ParagraphStyle('sc', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor(surplus_color), alignment=TA_RIGHT)),
            Paragraph(f'<font color="{"#1A6B3A" if s["on_track"] else "#8B1A1A"}"><b>{"ON TRACK" if s["on_track"] else "SHORTFALL"}</b></font>',
                      ParagraphStyle('st', fontName='Helvetica-Bold', fontSize=8, alignment=TA_CENTER)),
        ])

    t = Table(rows, colWidths=[1.1*inch, 1.2*inch, 1.0*inch, 0.9*inch, 1.1*inch, 1.1*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DARK_RED),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_GRAY),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('ALIGN', (0,0), (0,-1), 'LEFT'),
        ('ALIGN', (-1,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('FONTNAME', (0,1), (0,-1), 'Helvetica'),
        ('FONTSIZE', (0,1), (-1,-1), 8),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.2*inch))

    # Year-by-year for age 60 early SS scenario
    s60 = next((s for s in scenarios if s['label'] == 'age_60_early'), None)
    if s60:
        story.append(Paragraph('Year-by-Year Detail — Retire at 60 · SS at 62', styles['heading2']))
        story.append(Paragraph(f'Beginning portfolio balance at retirement: {fmt(s60["portfolio_at_retirement"])}', styles['body']))

        yhdr = [Paragraph(h, styles['table_hdr']) for h in
                [f'{p1} Age', f'{p2} Age', 'Year', 'Income Need', 'Pension', 'Soc. Security', 'Withdrawal', 'Portfolio Balance']]
        yrows = [yhdr]
        for yr in s60['yearly_detail']:
            if yr['jason_age'] % 2 == 0:  # every 2 years to fit page
                bal_color = '#1A6B3A' if yr['portfolio_balance'] > 0 else '#8B1A1A'
                yrows.append([
                    str(yr['jason_age']),
                    str(yr['justin_age']),
                    str(yr['year']),
                    fmt(yr['income_need']),
                    fmt(yr['pension']),
                    fmt(yr['social_security']),
                    fmt(yr['withdrawal']),
                    Paragraph(f'<font color="{bal_color}">{fmt(yr["portfolio_balance"])}</font>',
                              ParagraphStyle('pb', fontName='Helvetica', fontSize=7, textColor=colors.HexColor(bal_color), alignment=TA_RIGHT)),
                ])

        t2 = Table(yrows, colWidths=[0.65*inch, 0.65*inch, 0.55*inch, 0.85*inch, 0.85*inch, 0.85*inch, 0.85*inch, 1.1*inch])
        t2.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), DARK_RED),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
            ('GRID', (0,0), (-1,-1), 0.5, BORDER_GRAY),
            ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
            ('ALIGN', (0,0), (2,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('FONTSIZE', (0,1), (-1,-1), 7),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]))
        story.append(t2)

    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph(
        'Projected values are for planning purposes only and are not a promise of future performance. '
        'Calculated using long-term inflation rate of 2.00%.',
        styles['disclaimer']
    ))
    story.append(PageBreak())


# ── Section 3: Education ──────────────────────────────────────────────────────
def build_education(story, data, styles):
    status = _education_status(data)
    story += section_header('EDUCATION PLANNING', status, styles)

    goals = data.get('education', {}).get('goals', [])
    story.append(Paragraph(
        'Assumptions: 7.0% growth rate · 4.0% college cost inflation · 4 years at University of Nebraska-Lincoln. '
        '"Funding %"/"Gap" reflect a year-by-year simulation of the real drawdown, including continued growth '
        'during the 4 college years — a gap is only ever shown if the account is actually projected to run out.',
        styles['body']
    ))
    story.append(Spacer(1, 0.1*inch))

    hdr = [Paragraph(h, styles['table_hdr']) for h in
           ['Child', 'Current 529', 'Monthly Contrib.', 'Proj. at 18', 'Total Cost', 'Proj. Funding', 'Gap', 'Mo. to Close']]
    rows = [hdr]
    for g in goals:
        pct_color = '#1A6B3A' if g['funding_gap'] == 0 else '#B8760A'
        rows.append([
            g['child'],
            fmt(g['current_529_balance']),
            f"${g['monthly_contribution']:,.0f}/mo",
            fmt(g['projected_529_at_college']),
            fmt(g['projected_total_cost']),
            Paragraph(f'<font color="{pct_color}"><b>{g["funding_percent"]}%</b></font>',
                      ParagraphStyle('fp', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor(pct_color), alignment=TA_RIGHT)),
            fmt(g['funding_gap']),
            f"${g['monthly_savings_to_close_gap']:,.0f}/mo",
        ])

    t = Table(rows, colWidths=[0.7*inch, 0.85*inch, 0.9*inch, 0.85*inch, 0.85*inch, 0.7*inch, 0.7*inch, 0.9*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DARK_RED),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_GRAY),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('ALIGN', (0,0), (0,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.15*inch))

    # Kids Roth summary
    kids = data.get('kids', {}).get('kids', [])
    if kids:
        story.append(Paragraph('Kids Roth IRA Projections (529 rolls to Roth at graduation via SECURE 2.0)', styles['heading2']))
        khdr = [Paragraph(h, styles['table_hdr']) for h in
                ['Child', 'Roth Now', '$50/mo until 18', 'Roth at 18', '529 Rollover at 22', 'Roth at 60']]
        krows = [khdr]
        for k in kids:
            r = k['roth']
            krows.append([
                k['child'],
                fmt(r['current']),
                f"${r['monthly_contribution']:.0f}/mo",
                fmt(r['at_18']),
                fmt(r['529_rollover']),
                Paragraph(f'<b>{fmt(r["at_60"])}</b>',
                          ParagraphStyle('k60', fontName='Helvetica-Bold', fontSize=8, textColor=GREEN, alignment=TA_RIGHT)),
            ])
        kt = Table(krows, colWidths=[0.8*inch, 0.9*inch, 1.1*inch, 0.9*inch, 1.2*inch, 1.1*inch])
        kt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), DARK_RED),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
            ('GRID', (0,0), (-1,-1), 0.5, BORDER_GRAY),
            ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
            ('ALIGN', (0,0), (0,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('FONTSIZE', (0,1), (-1,-1), 8),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(kt)

    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph(
        'Projected values are for planning purposes only. Assumes current education accounts and any '
        'additional contributions are invested earning a projected 7.00% rate of return.',
        styles['disclaimer']
    ))
    story.append(PageBreak())


# ── Section 4: Risk Management ────────────────────────────────────────────────
def build_risk(story, data, styles):
    status = _risk_status(data)
    story += section_header('RISK MANAGEMENT', status, styles)

    ins = data.get('insurance', {})
    jason = ins.get('jason', {})
    justin = ins.get('justin', {})
    names = data.get('names', {})
    p1, p2 = names.get('person1', 'Person 1'), names.get('person2', 'Person 2')

    # Life insurance needs
    story.append(Paragraph('Life Insurance Analysis', styles['heading2']))
    lhdr = [Paragraph(h, styles['table_hdr']) for h in ['', f"In Event of {p1}'s Death", f"In Event of {p2}'s Death"]]
    lrows = [lhdr]
    items = [
        ('Debt Payoff',         fmt(jason.get('debt_payoff',0)),    fmt(justin.get('debt_payoff',0))),
        ('College Funding',     fmt(jason.get('college_funding',0)),fmt(justin.get('college_funding',0))),
        ('Income Replacement',  fmt(jason.get('income_replacement',0)), f'$0 ({p1} keeps earning)'),
        ('Total Need',          fmt(jason.get('total_need',0)),     fmt(justin.get('total_need',0))),
        ('Current Coverage',    fmt(jason.get('current_coverage',0)),fmt(justin.get('current_coverage',0))),
    ]
    for label, jval, usval in items:
        bold = label in ('Total Need', 'Current Coverage')
        fn = 'Helvetica-Bold' if bold else 'Helvetica'
        lrows.append([
            Paragraph(label, ParagraphStyle('ll', fontName=fn, fontSize=8, textColor=DARK_GRAY)),
            Paragraph(jval,  ParagraphStyle('lv', fontName=fn, fontSize=8, textColor=DARK_GRAY, alignment=TA_RIGHT)),
            Paragraph(usval, ParagraphStyle('lv2', fontName=fn, fontSize=8, textColor=DARK_GRAY, alignment=TA_RIGHT)),
        ])

    # Surplus/gap row
    j_surplus = jason.get('surplus_gap', 0)
    u_surplus = justin.get('surplus_gap', 0)
    jc = '#1A6B3A' if j_surplus >= 0 else '#8B1A1A'
    uc = '#1A6B3A' if u_surplus >= 0 else '#8B1A1A'
    lrows.append([
        Paragraph('Surplus / (Gap)', ParagraphStyle('sg', fontName='Helvetica-Bold', fontSize=8, textColor=DARK_GRAY)),
        Paragraph(f'<font color="{jc}"><b>{fmt(j_surplus)}</b></font>',
                  ParagraphStyle('jsg', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor(jc), alignment=TA_RIGHT)),
        Paragraph(f'<font color="{uc}"><b>{fmt(u_surplus)}</b></font>',
                  ParagraphStyle('usg', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor(uc), alignment=TA_RIGHT)),
    ])

    lt = Table(lrows, colWidths=[1.8*inch, 2.3*inch, 2.3*inch])
    lt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DARK_RED),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [WHITE, LIGHT_GRAY]),
        ('BACKGROUND', (0,-1), (-1,-1), LIGHT_GRAY),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_GRAY),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(lt)
    story.append(Spacer(1, 0.15*inch))

    # Disability, LTC, and Umbrella/Property
    dis = ins.get('disability', {})
    ltc = ins.get('ltc', {})
    prop = ins.get('property', {})
    story.append(Paragraph('Disability, Long Term Care &amp; Property', styles['heading2']))
    umbrella_note = (
        f"Recommended {fmt(prop.get('recommended_umbrella', 0))} (≈ net worth)"
        if prop.get('umbrella_adequate') else
        f"{fmt(prop.get('umbrella_gap', 0))} short of recommended {fmt(prop.get('recommended_umbrella', 0))}"
    )
    other = [
        ['Disability', f"${dis.get('monthly_benefit',0):,}/mo to age {dis.get('to_age',65)}", dis.get('funded_by', '')],
        ['Long Term Care', f"${ltc.get('daily_benefit',0)}/day · max {fmt(ltc.get('max_benefit',0))}", f"${ltc.get('premium_annual',0):,}/yr"],
        ['Umbrella Policy', fmt(prop.get('umbrella', 0)), umbrella_note],
    ]
    ot = Table(other, colWidths=[1.8*inch, 2.8*inch, 1.8*inch])
    ot.setStyle(TableStyle([
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [WHITE, LIGHT_GRAY]),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_GRAY),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
    ]))
    story.append(ot)
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph('These results are hypothetical and are not a promise of future performance.', styles['disclaimer']))
    story.append(PageBreak())


# ── Section 5: Estate Planning ────────────────────────────────────────────────
def build_estate(story, data, styles):
    story += section_header('ESTATE PLANNING', 'ATTENTION', styles)
    names = data.get('names', {})
    p1, p2 = names.get('person1', 'Person 1'), names.get('person2', 'Person 2')

    story.append(Paragraph(
        '⚠ Review estate document dates on the Estate Planning page annually.',
        ParagraphStyle('warn', fontName='Helvetica-Bold', fontSize=9, textColor=colors.HexColor('#B8760A'), spaceAfter=10)
    ))

    docs = [
        ['Document', 'Status', 'Date'],
        ['Joint Revocable Living Trust', 'See Estate Planning', '—'],
        [f'Wills ({p1} &amp; {p2})', 'See Estate Planning', '—'],
        ['Financial Power of Attorney', 'See Estate Planning', '—'],
        ['Health Care Power of Attorney', 'See Estate Planning', '—'],
        ['Credit Freeze (all 3 bureaus)', 'Verify annually', '—'],
    ]
    dt = Table(docs, colWidths=[3*inch, 1.8*inch, 1.5*inch])
    dt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DARK_RED),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_GRAY),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTCOLOR', (0,0), (-1,0), WHITE),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(dt)
    story.append(Spacer(1, 0.15*inch))

    story.append(Paragraph('Action Items', styles['heading2']))
    action_items = [
        '• Review and update estate documents periodically',
        '• Verify beneficiary designations on all accounts (401k, brokerage, HSA, life insurance)',
        '• Confirm credit freeze is active at Equifax, Experian, and TransUnion',
        '• Verify all investment accounts and real estate are titled in the name of the trust',
    ]
    for item in action_items:
        story.append(Paragraph(item, styles['body']))

    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph(
        'The material herein is based on information provided by the client.',
        styles['disclaimer']
    ))


# ── Main generator ────────────────────────────────────────────────────────────
def generate_annual_report(data: dict) -> bytes:
    buf = io.BytesIO()
    report_date = datetime.now().strftime('%B %d, %Y')
    names = data.get('names', {})
    client_name = f"{names.get('person1', 'Person 1')} & {names.get('person2', 'Person 2')}"

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=0.9*inch,
        bottomMargin=0.7*inch,
        title=f'Annual Financial Dashboard — {client_name}',
        author='Personal CFO',
    )

    styles = make_styles()
    story = []

    build_cover(story, data, styles)
    build_investments(story, data, styles)
    build_fi(story, data, styles)
    build_education(story, data, styles)
    build_risk(story, data, styles)
    build_estate(story, data, styles)

    class BoundCanvas(ReportCanvas):
        def __init__(self, filename, **kwargs):
            super().__init__(filename, name=client_name, date=report_date, **kwargs)

    doc.build(story, canvasmaker=BoundCanvas)
    buf.seek(0)
    return buf.read()

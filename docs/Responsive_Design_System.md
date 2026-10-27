# ProcureSphere 360 — Responsive Design System Report

**Document Reference:** DOC-RDS-PS360-D30  
**PRD Reference:** HPE-PRD-2026-PROC01 (VER-1.0-DRAFT)  
**Target Milestone:** Days 16–30 Checkpoint — UI Architecture & Responsive Design System  
**Issuing Authority:** HPE Team  
**Development Partner:** VPD Technologies Pvt. Ltd.  
**Repository:** `https://github.com/VPDTechnologies/ProcureSphere_360`  
**Assessment Date:** 2026-10-26  
**Status:** Verification-Ready Documentation  

---

## 1. Executive Summary

This report documents the **Responsive Design System** implemented across ProcureSphere 360. 

In alignment with the PRD's frontend specifications and repository governance rules, the application standardizes on a **unified UI architecture**: **Django Server-Side Templates + Bootstrap 5.3.2 + HTMX 1.9.10 + Vanilla CSS + FontAwesome 6.5.1 + Bootstrap Icons 1.11.3 + Chart.js 4.4.1**.

The design system eliminates disjointed styling, inconsistent ad-hoc colors, and unmanaged layout overflow, replacing them with a curated token-based design system tailored for enterprise procurement dashboards.

---

## 2. Design Tokens & Visual Hierarchy

All design tokens are centralized within `static/css/style.css` using standard CSS Custom Properties (`:root`):

### 2.1 Color Palette Tokens

```css
:root {
    /* Primary Brand & Enterprise Palette */
    --hpe-navy: #002B49;      /* Primary brand header, key titles, active nav */
    --hpe-cyan: #00B0B9;      /* Brand accent, primary CTAs, highlight pills */
    --hpe-dark-bg: #0F172A;   /* Slate dark background for executive headers */
    --hpe-card-bg: #1E293B;   /* Slate card background for dark contrast cards */

    /* Semantic Status & Indicator Palette */
    --hpe-emerald: #10B981;   /* Success, approved, active, compliant */
    --hpe-amber: #F59E0B;     /* Warning, pending approval, watchlist */
    --hpe-rose: #F43F5E;      /* Danger, rejected, critical risk, suspended */
    --hpe-indigo: #6366F1;    /* Informational, audit, workflow transitions */

    /* Typography */
    --font-heading: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif;
    --font-body: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}
```

### 2.2 Semantic Color Mapping Matrix

| Token Name | Hex Value | Usage / Semantic Context | Foreground / Contrast |
|:---|:---:|:---|:---:|
| `--hpe-navy` | `#002B49` | Primary navbar, brand identity, document titles, primary buttons. | `#FFFFFF` (WCAG AAA compliant) |
| `--hpe-cyan` | `#00B0B9` | Accent buttons (`.btn-cyan`), active tabs, KPI highlight badges. | `#0F172A` / `#FFFFFF` |
| `--hpe-emerald` | `#10B981` | Approved PRs, Active Vendors, Paid Invoices, Completed Receipts. | `#065F46` / `#FFFFFF` |
| `--hpe-amber` | `#F59E0B` | Pending review, Draft documents, Variance tolerance warnings. | `#92400E` / `#FFFFFF` |
| `--hpe-rose` | `#F43F5E` | Rejected PRs, Suspended Vendors, 3-Way Match Exceptions, Expired Contracts. | `#991B1B` / `#FFFFFF` |
| `--hpe-indigo` | `#6366F1` | Audit log traces, Sourcing event stages, Version change orders. | `#FFFFFF` |

---

## 3. Typography & Hierarchy

The application establishes a clear typographic hierarchy separating display headers from data-dense tables:

1. **Heading Typography (`--font-heading`):**
   - Family: `'Outfit'`, `-apple-system`, `BlinkMacSystemFont`, `sans-serif`
   - Weight: `600` (Semi-bold) to `700` (Bold)
   - Letter Spacing: `-0.02em` for modern geometric clarity
   - Elements: `h1`, `h2`, `h3`, `h4`, `h5`, `h6`, `.metric-value`, `.font-heading`
2. **Body Typography (`--font-body`):**
   - Family: `'Inter'`, `-apple-system`, `BlinkMacSystemFont`, `sans-serif`
   - Weight: `400` (Regular) and `500` (Medium)
   - Line Height: `1.5`
   - Background Body: `#F8FAFC` (Slate 50) with high contrast text `#1E293B` (Slate 800)
3. **Data & Code Display:**
   - Monospace font for document numbers (`PR-2026-00001`, `PO-2026-00001`, `GRN-2026-00001`), tax identifiers, and transaction hashes:
   - Font: `ui-monospace`, `SFMono-Regular`, `Consolas`, `monospace`

---

## 4. Reusable Component Catalog

All components are constructed using predefined CSS classes in `static/css/style.css` rather than ad-hoc inline styles:

### 4.1 Cards & Elevation
- **`.glass-card`:** White background (`#FFFFFF`), subtle border (`#E2E8F0`), 14px border radius, soft elevation (`box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.05)`). Used for form containers and data tables.
- **`.glass-card-dark`:** Dark slate gradient (`linear-gradient(135deg, #0F172A 0%, #1E293B 100%)`) with white text and light translucent border (`rgba(255, 255, 255, 0.1)`). Used for executive overview dashboards and key metrics banners.
- **`.stat-card`:** Specialized KPI metric tile with hover elevation transition (`transform: translateY(-4px)`).

### 4.2 Metrics & KPI Avatars
- **`.metric-value`:** Large bold KPI value display (`font-size: 1.85rem`, `font-weight: 700`, `--hpe-navy`).
- **`.metric-label`:** Uppercase tracking subtitle (`font-size: 0.8rem`, `letter-spacing: 0.05em`, `#64748B`).
- **`.icon-avatar`:** 46x46px rounded icon container (`border-radius: 12px`) with color variants:
  - `.icon-avatar-primary`: Navy background tint with navy icon.
  - `.icon-avatar-success`: Emerald tint with green icon.
  - `.icon-avatar-warning`: Amber tint with orange icon.
  - `.icon-avatar-info`: Cyan tint with cyan icon.
  - `.icon-avatar-danger`: Rose tint with red icon.

### 4.3 Status Badges & Pills
- **`.badge-status`:** Rounded pill badge (`border-radius: 9999px`, `font-size: 0.75rem`, `font-weight: 600`).
  - `.badge-status-active`, `.badge-status-approved`: Light emerald background (`#D1FAE5`) with dark green text (`#065F46`).
  - `.badge-status-pending`, `.badge-status-draft`: Light amber background (`#FEF3C7`) with dark amber text (`#92400E`).
  - `.badge-status-suspended`, `.badge-status-rejected`: Light rose background (`#FEE2E2`) with dark red text (`#991B1B`).

### 4.4 Data Tables & Overflow
- **`.table-responsive`:** Enforces `-webkit-overflow-scrolling: touch` and `max-width: 100%` on all data tables, preventing horizontal page breakage on narrow viewports.
- **`.table-hover`:** Row highlighting on hover with consistent contrast.

### 4.5 HTMX Micro-Interactions & Loading States
- **`.notif-item`:** Smooth opacity and transform transitions (`transition: opacity 0.4s ease, transform 0.4s ease`).
- **`.htmx-swapping`:** Fades out and slides right (`opacity: 0; transform: translateX(24px)`) when items are marked read or approved.
- **`.btn-loading`:** Spinner loading state disabling double-submits during async HTMX post actions.

---

## 5. Layout Architecture & Responsive Breakpoints

ProcureSphere 360 implements a multi-tier layout structure adapting seamlessly across device classes:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Desktop Viewport (>= 992px)                              │
│ ┌───────────────┬─────────────────────────────────────────┐ │
│ │ Left Sidebar  │ Top Navbar / Breadcrumbs / Quick Actions │ │
│ │ (Brand, Nav,  ├─────────────────────────────────────────┤ │
│ │ Portals, User)│ 4-Column Stat Card Grid (25% / col)     │ │
│ │               ├─────────────────────────────────────────┤ │
│ │               │ Main Content / 8-Column Data Table Grid │ │
│ └───────────────┴─────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ 2. Tablet Viewport (768px - 991px)                          │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Collapsed Hamburger Navbar (Sidebar converts to offcanvas)│
│ ├─────────────────────────────────────────────────────────┤ │
│ │ 2-Column Stat Card Grid (50% / col)                     │ │
│ ├─────────────────────────────────────────────────────────┤ │
│ │ Full-Width Responsive Table Container (Touch scroll)   │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ 3. Mobile Viewport (< 768px)                                │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Compact Brand Header (1rem font, collapsed menu)        │ │
│ ├─────────────────────────────────────────────────────────┤ │
│ │ 1-Column Stacked Stat Cards (100% width, 0.85rem padding)│
│ ├─────────────────────────────────────────────────────────┤ │
│ │ Scaled KPI Metric (1.35rem font)                        │ │
│ ├─────────────────────────────────────────────────────────┤ │
│ │ Scrolled Table View with horizontal touch boundary      │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 5.1 Standard Breakpoint Matrix

| Viewport Tier | Breakpoint Query | Layout Adaptation Behavior | Verification Status |
|:---|:---|:---|:---:|
| **Mobile (XS / SM)** | `< 768px` | - Navbar brand downscales to `1rem`.<br>- Stat cards downscale padding to `0.85rem` and border-radius to `10px`.<br>- Metric values downscale from `1.85rem` to `1.35rem`.<br>- Metric cards stack in a single column (100% width).<br>- Tables scroll horizontally inside `.table-responsive` without viewport breaking.<br>- Global `overflow-x: clip` prevents horizontal window wobble. | **VERIFIED** |
| **Tablet (MD)** | `768px – 991.98px` | - Desktop sidebar hidden via `.sidebar-desktop { display: none !important; }`.<br>- Nav links collapse into Bootstrap collapsible hamburger menu.<br>- Stat card grids arrange in 2-column layout (`col-md-6`).<br>- Action buttons stack vertically or use compact icon dropdowns. | **VERIFIED** |
| **Desktop (LG)** | `992px – 1199.98px` | - Full sidebar / top navbar navigation displayed.<br>- 4-column metric grids (`col-lg-3`).<br>- Full multi-column data tables with sortable headers.<br>- Filter toolbars arrange in inline horizontal flex rows. | **VERIFIED** |
| **Large Desktop (XL / XXL)** | `>= 1200px` | - Centered layout with max container width constraints (`container-fluid px-4`).<br>- Split pane views (e.g. Contract Detail + Obligations panel; Requisition Line Items + Budget Bar). | **VERIFIED** |

---

## 6. Viewport Verification Evidence & Cross-Device Review

| Device / Viewport Tested | Dimensions | Layout / Functional Checks Performed | Result |
|:---|:---:|:---|:---:|
| **Standard Desktop** | 1920 x 1080 | Full sidebar layout; 4-card KPI row; audit diff modal; multi-tier tables. | **PASS** — Clean layout, zero overflow. |
| **Laptop Display** | 1366 x 768 | Top navbar layout; filter forms align horizontally; Charts resize properly. | **PASS** — Chart.js auto-resizes. |
| **Tablet Portrait (iPad)** | 768 x 1024 | Sidebar collapses; 2-column KPI grid; tables touch scroll horizontally. | **PASS** — Hamburger menu opens cleanly. |
| **Mobile Standard (iPhone / Pixel)** | 375 x 812 | Single-column card stack; scaled headers (1rem); touch-scrollable tables. | **PASS** — Zero horizontal page blowout. |
| **Compact Mobile (SE)** | 320 x 568 | Stat cards compact; buttons wrap cleanly; badges fit on single line. | **PASS** — Text does not clip. |

---

## 7. Unverified Breakpoints & Visual Polish Backlog

While all standard mobile, tablet, and desktop viewports are implemented and verified, the following edge cases remain marked as **PENDING VERIFICATION**:

1. **Ultra-Wide Workstations (> 2560px):**
   - *Status:* Unverified on physical hardware.
   - *Risk:* Fluid containers may stretch excessively on 4K monitors without a `max-width: 1920px` wrapper constraint.
2. **Complex 8-Column Comparison Grids on Landscape Mobile (480px – 640px):**
   - *Status:* Touch scroll works; however, column freezing (sticky left column for item description) is not yet implemented for RFQ bid comparison tables.
3. **Dark Mode Toggle:**
   - *Status:* Dark styling exists for executive cards (`.glass-card-dark`), but a user-switchable global dark theme is planned for Phase 3.

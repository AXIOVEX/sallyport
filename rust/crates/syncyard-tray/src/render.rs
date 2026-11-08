//! Rendering rules (spec 035 sections 4-5): the tooltip
//! budget, the shared vocabulary, icon marks as independent
//! dimensions, and the menu skeleton. Everything here is a
//! pure function of an observation: rendering happens on-change
//! only, never per tick (FR-035-05).

use crate::connection::LinkState;

/// OS tray tooltip cap; one line, whole parts only.
pub const TOOLTIP_BUDGET: usize = 63;

/// The exact shared status vocabulary (FR-035-10).
pub mod words {
    pub const UP: &str = "up";
    pub const DOWN: &str = "down";
    pub const LINKED: &str = "linked";
    pub const LINK_STALE: &str = "link stale";
    pub const UNLINKED: &str = "unlinked";
}

/// Compose the tooltip from parts in fixed priority order:
/// parts are dropped **whole**, lowest priority first, until
/// the line fits the budget — never truncated mid-word
/// (FR-035-09). If the highest-priority part alone exceeds the
/// budget it is returned whole (a part is never cut).
pub fn compose_tooltip(parts: &[String]) -> String {
    let mut kept: Vec<&str> = parts.iter().map(|s| s.as_str()).collect();
    loop {
        let line = kept.join(", ");
        if line.chars().count() <= TOOLTIP_BUDGET || kept.len() <= 1 {
            return line;
        }
        kept.pop();
    }
}

/// Runner-health mark (one independent icon dimension).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HealthMark {
    Up,
    Down,
}

impl HealthMark {
    pub fn word(&self) -> &'static str {
        match self {
            HealthMark::Up => words::UP,
            HealthMark::Down => words::DOWN,
        }
    }
}

/// Connection mark (the other independent icon dimension).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LinkMark {
    Linked,
    LinkStale,
    Unlinked,
}

impl From<LinkState> for LinkMark {
    fn from(s: LinkState) -> Self {
        match s {
            LinkState::Connected => LinkMark::Linked,
            LinkState::Stale => LinkMark::LinkStale,
            LinkState::Disconnected => LinkMark::Unlinked,
        }
    }
}

impl LinkMark {
    pub fn word(&self) -> &'static str {
        match self {
            LinkMark::Linked => words::LINKED,
            LinkMark::LinkStale => words::LINK_STALE,
            LinkMark::Unlinked => words::UNLINKED,
        }
    }
}

/// The icon state: health and connection as independent marks,
/// never collapsed into one color (FR-035-05).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct IconMarks {
    pub health: HealthMark,
    pub link: LinkMark,
}

/// What a render pass would draw. Compared across ticks: an
/// unchanged observation produces an identical RenderState and
/// the shell skips the redraw.
#[derive(Debug, Clone, PartialEq)]
pub struct RenderState {
    pub icon: IconMarks,
    pub tooltip: String,
    pub menu_fingerprint: String,
}

/// One menu row. Rows are data; the platform shell draws them.
#[derive(Debug, Clone, PartialEq)]
pub struct MenuItem {
    pub label: String,
    pub kind: MenuKind,
    /// False = greyed. A greyed item ALWAYS carries a visible
    /// reason (FR-035-13); a feature the app lacks is omitted
    /// from the menu entirely, never greyed.
    pub enabled: bool,
    pub reason: Option<String>,
    /// Verb the row invokes, for action rows.
    pub action: Option<String>,
    /// True only for rows that flip a supervisor permission —
    /// the sole rows allowed the Enable/Disable verbs
    /// (FR-035-12).
    pub supervisor_permission: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MenuKind {
    /// A status line (never an action, never "disabled").
    Header,
    Action,
    Settings,
    Updates,
    Exit,
}

impl MenuItem {
    pub fn header(label: impl Into<String>) -> Self {
        MenuItem {
            label: label.into(),
            kind: MenuKind::Header,
            enabled: true,
            reason: None,
            action: None,
            supervisor_permission: false,
        }
    }

    pub fn action(label: impl Into<String>, action: impl Into<String>) -> Self {
        MenuItem {
            label: label.into(),
            kind: MenuKind::Action,
            enabled: true,
            reason: None,
            action: Some(action.into()),
            supervisor_permission: false,
        }
    }

    /// A greyed action: unavailable NOW, with the reason
    /// visible (FR-035-13).
    pub fn unavailable(label: impl Into<String>, reason: impl Into<String>) -> Self {
        MenuItem {
            label: label.into(),
            kind: MenuKind::Action,
            enabled: false,
            reason: Some(reason.into()),
            action: None,
            supervisor_permission: false,
        }
    }
}

/// Inputs for the menu skeleton, in spec order.
pub struct MenuInput {
    /// Status header lines (identity, connection, fleet).
    pub headers: Vec<String>,
    /// The Open row, present ONLY where a surface exists
    /// (FR-035-11: features without a surface are omitted).
    pub open_label: Option<String>,
    /// Per-item controls (service Start/Stop etc.).
    pub controls: Vec<MenuItem>,
    /// What Exit does to supervised things, stated on its row.
    pub exit_effect: String,
}

/// Build the menu in the spec's skeleton order (FR-035-11):
/// status header; Open where a surface exists; per-item
/// controls; Settings; Check for updates; Exit last, always
/// enabled, its effect stated in the label (FR-035-15).
pub fn build_menu(input: &MenuInput) -> Vec<MenuItem> {
    let mut menu: Vec<MenuItem> = Vec::new();
    for h in &input.headers {
        menu.push(MenuItem::header(h.clone()));
    }
    if let Some(open) = &input.open_label {
        menu.push(MenuItem::action(open.clone(), "open"));
    }
    menu.extend(input.controls.iter().cloned());
    let mut settings = MenuItem::action("Settings", "settings");
    settings.kind = MenuKind::Settings;
    menu.push(settings);
    let mut updates = MenuItem::action("Check for updates", "check-updates");
    updates.kind = MenuKind::Updates;
    menu.push(updates);
    let mut exit = MenuItem::action(format!("Exit — {}", input.exit_effect), "exit");
    exit.kind = MenuKind::Exit;
    menu.push(exit);
    menu
}

/// A stable fingerprint of a menu, for on-change rendering.
pub fn menu_fingerprint(menu: &[MenuItem]) -> String {
    let mut s = String::new();
    for item in menu {
        s.push_str(&item.label);
        s.push('|');
        s.push_str(if item.enabled { "1" } else { "0" });
        s.push(';');
    }
    s
}

//! The in-memory journal: what this application did to a device, in order.
//!
//! `psafety` emits an entry for every operation it authorises and hands it to a
//! sink the caller supplies. This is that sink. The arrangement is why
//! `psafety` does not depend on this crate -- it sits below it, and an edge the
//! other way would reverse the DAG (see the note in `psafety/Cargo.toml`).
//!
//! # The payload rule, enforced rather than described
//!
//! No entry stores payload bytes, and there is a test rather than only this
//! paragraph. The reason is concrete: a payload can carry a keymap, a keymap can
//! carry a macro, and a macro can carry a password. This record is meant to be
//! readable by the user and attachable to a bug report, and those two facts
//! together mean payload bytes must never be in it. `psafety::JournalEntry`
//! carries `payload_len` and no payload, so the rule is upheld by the type this
//! crate receives; the test below is what stops that changing quietly.

use psafety::journal::{JournalEntry, JournalSink, Outcome};

/// Everything the gate has authorised, in the order it happened.
///
/// In memory only for now. Persistence, rotation and the on-disk format belong
/// with the first write path (TICKET-15): a journal whose purpose is to show a
/// user what was changed on their hardware has nothing to persist until
/// something can change it.
#[derive(Default)]
pub struct JournalLog {
    entries: Vec<JournalEntry>,
}

impl JournalLog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn entries(&self) -> &[JournalEntry] {
        &self.entries
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// The most recent entries, newest first, for a status panel.
    pub fn recent(&self, limit: usize) -> impl Iterator<Item = &JournalEntry> {
        self.entries.iter().rev().take(limit)
    }

    /// Entries that did not complete.
    ///
    /// Its own accessor because "what went wrong" is the question a person asks
    /// of a journal, and making them filter for it invites a UI that shows
    /// everything and highlights nothing.
    pub fn failures(&self) -> impl Iterator<Item = &JournalEntry> {
        self.entries
            .iter()
            .filter(|entry| !matches!(entry.outcome, Outcome::Completed))
    }

    /// One line per entry, in the terms a person reads rather than a debug dump.
    ///
    /// Deliberately here rather than in the UI: what a journal line says is a
    /// product decision, and two front-ends rendering it differently would make
    /// a bug report ambiguous about what actually happened.
    pub fn render(&self) -> Vec<String> {
        self.entries.iter().map(render_entry).collect()
    }
}

/// One entry as a person reads it.
///
/// Public because a table of entries is rendered a row at a time, and a caller
/// that had only [`JournalLog::render`] would either re-derive the wording or
/// index into a parallel `Vec<String>` by position. Both of those are ways for
/// two front-ends to disagree about what happened, which is the thing this
/// function existing in this crate prevents.
pub fn render_entry(entry: &JournalEntry) -> String {
    let outcome = match entry.outcome {
        Outcome::Completed => "ok".to_string(),
        Outcome::Rejected => "answer failed validation".to_string(),
        Outcome::Refused(refusal) => format!("refused: {refusal:?}"),
        Outcome::Failed(kind) => format!("failed: {kind:?}"),
    };
    format!(
        "{} ms  {}::{}  [{}]  {}",
        entry.at_ms,
        entry.family,
        entry.command,
        entry.class.as_str(),
        outcome
    )
}

impl JournalSink for JournalLog {
    fn record(&mut self, entry: JournalEntry) {
        self.entries.push(entry);
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;
    use psafety::OpcodeClass;
    use psafety::journal::{Intent, Verification};
    use ptransport::DeviceId;

    fn entry(command: &'static str, outcome: Outcome) -> JournalEntry {
        JournalEntry {
            at_ms: 10,
            device: DeviceId::new(1),
            family: "aula-bytech",
            command,
            class: OpcodeClass::SafeRead,
            intent: Intent::Read,
            payload_len: 8,
            outcome,
            verification: Verification::NotApplicable,
        }
    }

    #[test]
    fn a_rendered_line_never_contains_payload_bytes() {
        // The rule, as a test rather than as a paragraph. There is nothing to
        // redact here because the entry type carries a length and not the bytes
        // -- which is the point. If a payload field ever appears on
        // `JournalEntry`, this test is where the reason it must not is written
        // down.
        let mut log = JournalLog::new();
        log.record(entry("read_key_travel", Outcome::Completed));
        let line = log.render().pop().expect("one line");
        assert!(line.contains("read_key_travel"));
        assert!(line.contains("safe_read"));
        assert!(
            !line.contains("payload"),
            "the payload length leaked into a user-facing line: {line}"
        );
    }

    #[test]
    fn failures_are_reachable_without_filtering_by_hand() {
        let mut log = JournalLog::new();
        log.record(entry("read_model_id", Outcome::Completed));
        log.record(entry("read_key_travel", Outcome::Rejected));
        assert_eq!(log.len(), 2);
        let failures: Vec<_> = log.failures().map(|e| e.command).collect();
        assert_eq!(failures, ["read_key_travel"]);
    }

    #[test]
    fn recent_is_newest_first() {
        let mut log = JournalLog::new();
        log.record(entry("read_model_id", Outcome::Completed));
        log.record(entry("read_key_travel", Outcome::Completed));
        let recent: Vec<_> = log.recent(1).map(|e| e.command).collect();
        assert_eq!(recent, ["read_key_travel"]);
    }
}

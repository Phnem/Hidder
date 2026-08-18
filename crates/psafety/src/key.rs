//! What identifies a command on the wire.
//!
//! # Why this is not a `u8`
//!
//! It was one until TICKET-12. The first family whose commands are not single
//! bytes broke it, and broke it in the way that matters: in `aula-bytech` a
//! command is a *pair* -- a group byte and a subcommand byte -- and the group
//! `0x82` covers at least sixteen subcommands, most of which nobody has
//! classified. Authorising the group in order to reach one subcommand would
//! have granted all of them, which is precisely the hole this crate exists to
//! not have.
//!
//! # Why this is not `Vec<u8>`
//!
//! Because then a payload could become a command. The whole boundary rests on
//! the identity of a command being something the caller cannot supply: it comes
//! from the generated table and nowhere else. A variable-length byte string in
//! the public API is an invitation to pass one in, so the type enumerates the
//! shapes the ACL can express and no others. A family that needs a third shape
//! adds a variant here, in a diff someone reviews.

/// How one family addresses one command.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub enum CommandKey {
    /// A single opcode byte, the shape the ROYUAN families use.
    Opcode(u8),
    /// A group byte plus a subcommand byte, the shape `aula-bytech` uses.
    ///
    /// The two bytes are one identity. There is deliberately no accessor that
    /// returns the group on its own: a caller holding a group byte is a caller
    /// one step away from addressing a subcommand nobody classified.
    GroupSubcommand { group: u8, subcommand: u8 },
}

impl CommandKey {
    /// The bytes this key contributes to the head of a frame, in order.
    ///
    /// The only way the bytes leave this type. Codecs place them at
    /// family-specific offsets -- for `aula-bytech` they are frame bytes 0 and
    /// 1 -- so this says what they are and not where they go.
    pub fn header_bytes(self) -> HeaderBytes {
        match self {
            CommandKey::Opcode(opcode) => HeaderBytes {
                bytes: [opcode, 0],
                len: 1,
            },
            CommandKey::GroupSubcommand { group, subcommand } => HeaderBytes {
                bytes: [group, subcommand],
                len: 2,
            },
        }
    }
}

/// The one or two bytes a [`CommandKey`] puts at the head of a frame.
///
/// Its own type rather than a `Vec<u8>` so that nothing can grow it: a codec
/// receives exactly what the ACL authorised and has nothing to append to.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct HeaderBytes {
    bytes: [u8; 2],
    len: u8,
}

impl HeaderBytes {
    pub fn as_slice(&self) -> &[u8] {
        &self.bytes[..usize::from(self.len)]
    }
}

impl std::ops::Deref for HeaderBytes {
    type Target = [u8];

    fn deref(&self) -> &[u8] {
        self.as_slice()
    }
}

impl std::fmt::Display for CommandKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CommandKey::Opcode(opcode) => write!(f, "{opcode:#04x}"),
            CommandKey::GroupSubcommand { group, subcommand } => {
                write!(f, "{group:#04x}:{subcommand:#04x}")
            }
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    #[test]
    fn a_group_is_not_equal_to_any_of_its_subcommands() {
        // The hole this type closes: authorising `0x82` must not be the same
        // value as authorising `0x82:0x01`, or the ACL cannot express the
        // difference and granting one grants the other.
        let group_only = CommandKey::Opcode(0x82);
        for subcommand in 0u8..=255 {
            assert_ne!(
                group_only,
                CommandKey::GroupSubcommand {
                    group: 0x82,
                    subcommand
                }
            );
        }
    }

    #[test]
    fn subcommands_of_one_group_are_distinct() {
        assert_ne!(
            CommandKey::GroupSubcommand {
                group: 0x82,
                subcommand: 0x01
            },
            CommandKey::GroupSubcommand {
                group: 0x82,
                subcommand: 0x02
            }
        );
    }

    #[test]
    fn header_bytes_are_in_frame_order() {
        assert_eq!(CommandKey::Opcode(0x8F).header_bytes().as_slice(), &[0x8F]);
        assert_eq!(
            CommandKey::GroupSubcommand {
                group: 0x82,
                subcommand: 0x01
            }
            .header_bytes()
            .as_slice(),
            &[0x82, 0x01]
        );
    }

    #[test]
    fn it_renders_the_pair_as_a_pair() {
        assert_eq!(CommandKey::Opcode(0x8F).to_string(), "0x8f");
        assert_eq!(
            CommandKey::GroupSubcommand {
                group: 0x82,
                subcommand: 0x01
            }
            .to_string(),
            "0x82:0x01"
        );
    }
}

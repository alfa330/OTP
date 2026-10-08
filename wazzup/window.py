"""Customer message time for the WABA 24-hour reply window."""


def last_inbound_at(cursor, account, channel_id, chat_id):
    """Read the original customer timestamp, independently of displayed history.

    Late delivery can move ``dt`` to the arrival time; ``wazzup_dt`` preserves
    the actual WhatsApp timestamp. Deleting a message does not undo its window.
    The existing chat index bounds this lookup to one chat's messages.
    """
    cursor.execute("""
        SELECT MAX(COALESCE(wazzup_dt, dt))
          FROM wazzup_messages
         WHERE account=%s AND channel_id=%s AND chat_id=%s AND NOT is_echo
    """, (account, channel_id, chat_id))
    value = cursor.fetchone()[0]
    return value.isoformat() if hasattr(value, 'isoformat') else value

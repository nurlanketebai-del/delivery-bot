
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_note_entries (
                id BIGSERIAL PRIMARY KEY,
                note_text TEXT NOT NULL,
                created_by BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

import sqlite3
conn = sqlite3.connect('nlb_results.db')
deleted = conn.execute(
    "DELETE FROM lottery_results WHERE lottery_name = 'Mahajana Sampatha'"
).rowcount
conn.execute(
    "DELETE FROM individual_numbers WHERE lottery_name = 'Mahajana Sampatha'"
)
conn.commit()
remaining = conn.execute('SELECT COUNT(*) FROM lottery_results').fetchone()[0]
print(f'Deleted {deleted} bad rows. Remaining in DB: {remaining}')
conn.close()

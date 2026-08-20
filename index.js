require('dotenv').config();
const { Telegraf, session } = require('telegraf');
const { Pool } = require('pg');
const http = require('http');

const BOT_TOKEN = process.env.BOT_TOKEN || '8870239268:AAGCo5O4FsnKIQd6Hi1hJFLTUgHtc1rPfjc';
const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://neondb_owner:npg_d7L4xXgESPUR@ep-frosty-water-axd2u33z-pooler.c-4.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require';
const ADMIN_ID = parseInt(process.env.ADMIN_ID || '8288170669');
const BOT_NAME = process.env.BOT_NAME || 'Gmail Farmer';
const BOT_USERNAME = process.env.BOT_USERNAME || 'ChaliesTaskBot';

const pool = new Pool({ connectionString: DATABASE_URL, ssl: { rejectUnauthorized: false } });
const bot = new Telegraf(BOT_TOKEN);

bot.use(session());

let adminWaitingForTask = false;

async function initDB() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS users (
      telegram_id BIGINT PRIMARY KEY,
      name TEXT NOT NULL,
      payment_method TEXT NOT NULL,
      payment_number TEXT NOT NULL,
      balance INTEGER DEFAULT 0,
      invited_by BIGINT,
      registered_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS tasks (
      id SERIAL PRIMARY KEY,
      first_name TEXT,
      last_name TEXT,
      email TEXT UNIQUE NOT NULL,
      password TEXT,
      birth_year TEXT,
      status TEXT DEFAULT 'available',
      assigned_to BIGINT,
      assigned_at TIMESTAMP,
      expires_at TIMESTAMP,
      completed_at TIMESTAMP,
      created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS withdrawals (
      id SERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL,
      amount INTEGER NOT NULL,
      payment_method TEXT,
      payment_number TEXT,
      status TEXT DEFAULT 'pending',
      requested_at TIMESTAMP DEFAULT NOW(),
      paid_at TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS referral_rewards (
      id SERIAL PRIMARY KEY,
      referrer_id BIGINT NOT NULL,
      rewarded_at TIMESTAMP DEFAULT NOW()
    );
    ALTER TABLE users ADD COLUMN IF NOT EXISTS invited_by BIGINT;
  `);
  console.log('Database initialized.');
}

setInterval(async () => {
  try {
    const res = await pool.query(`
      UPDATE tasks SET status='available', assigned_to=NULL,
      assigned_at=NULL, expires_at=NULL
      WHERE status='assigned' AND expires_at < NOW()
      RETURNING id, assigned_to
    `);
    if (res.rows.length > 0) console.log(`[expiry] Expired ${res.rows.length} task(s)`);
  } catch (e) {
    console.error('[expiry]', e.message);
  }
}, 60000);

// Check referral reward
async function checkReferralReward(referrerId) {
  try {
    // Count invitees
    const inviteesRes = await pool.query(
      'SELECT telegram_id FROM users WHERE invited_by=$1', [referrerId]
    );
    if (inviteesRes.rows.length < 10) return;

    // Count how many invitees completed at least 1 task
    const inviteeIds = inviteesRes.rows.map(r => r.telegram_id);
    const completedRes = await pool.query(`
      SELECT DISTINCT assigned_to FROM tasks
      WHERE status='completed' AND assigned_to = ANY($1)
    `, [inviteeIds]);

    if (completedRes.rows.length < 5) return;

    // Check if already rewarded
    const rewardedRes = await pool.query(
      'SELECT id FROM referral_rewards WHERE referrer_id=$1', [referrerId]
    );
    if (rewardedRes.rows.length > 0) return;

    // Give reward
    await pool.query('UPDATE users SET balance=balance+50 WHERE telegram_id=$1', [referrerId]);
    await pool.query('INSERT INTO referral_rewards (referrer_id) VALUES ($1)', [referrerId]);

    await bot.telegram.sendMessage(referrerId,
      '🎉 Referral reward! You invited 10 workers and 5+ completed tasks.\n+50 birr added to your balance!'
    );
    console.log(`[referral] Rewarded ${referrerId} with 50 birr`);
  } catch (e) {
    console.error('[referral]', e.message);
  }
}

function mainKeyboard() {
  return {
    reply_markup: {
      keyboard: [
        ['📋 Get Task', '✅ Done'],
        ['💰 My Balance', '💸 Withdraw'],
        ['📊 My Stats', '👥 My Invites'],
        ['❓ Help']
      ],
      resize_keyboard: true
    }
  };
}

function adminKeyboard() {
  return {
    reply_markup: {
      keyboard: [
        ['📋 Get Task', '✅ Done'],
        ['💰 My Balance', '💸 Withdraw'],
        ['👥 All Workers', '📦 All Tasks'],
        ['📊 My Stats', '👥 My Invites'],
        ['❓ Help']
      ],
      resize_keyboard: true
    }
  };
}

function keyboard(userId) {
  return userId === ADMIN_ID ? adminKeyboard() : mainKeyboard();
}

function getInviteLink(userId) {
  return `https://t.me/${BOT_USERNAME}?start=ref_${userId}`;
}

function formatTask(t) {
  const emailUser = t.email.replace('@gmail.com', '');
  return (
    `📋 *Your Task:*\n\n` +
    `👤 First name: \`${t.first_name}\`\n` +
    `👤 Last name: \`${t.last_name}\`\n` +
    `📧 Email: \`${emailUser}\`@gmail\\.com\n` +
    `🔑 Password: \`${t.password}\`\n` +
    `🎂 Birth year: \`${t.birth_year}\`\n\n` +
    `⚠️ Use exactly these details\\!\n` +
    `⏳ You have 2 hours to complete this task\\.\n\n` +
    `Press ✅ Done when you finish\\.`
  );
}

// --- /start ---
bot.start(async (ctx) => {
  const userId = ctx.from.id;
  const startPayload = ctx.startPayload;

  const res = await pool.query('SELECT * FROM users WHERE telegram_id=$1', [userId]);

  if (res.rows.length > 0) {
    return ctx.reply(`Welcome back, ${res.rows[0].name}! 👋\nUse the menu below.`, keyboard(userId));
  }

  // Store referrer if exists
  ctx.session = { step: 'reg_name' };
  if (startPayload && startPayload.startsWith('ref_')) {
    const referrerId = parseInt(startPayload.replace('ref_', ''));
    if (referrerId !== userId) {
      ctx.session.invited_by = referrerId;
    }
  }

  return ctx.reply(`👋 Welcome to ${BOT_NAME}!\n\nLet's register you.\n\nWhat is your full name?`);
});

// --- /addtask ---
bot.command('addtask', async (ctx) => {
  if (ctx.from.id !== ADMIN_ID) return;
  adminWaitingForTask = true;
  return ctx.reply(
    '📋 Now forward or paste task details\\. Send /donetask when finished adding tasks\\.',
    { parse_mode: 'MarkdownV2' }
  );
});

// --- /donetask ---
bot.command('donetask', async (ctx) => {
  if (ctx.from.id !== ADMIN_ID) return;
  adminWaitingForTask = false;
  return ctx.reply('✅ Done adding tasks.');
});

// --- /confirm ---
bot.hears(/^\/confirm_(\d+)$/, async (ctx) => {
  if (ctx.from.id !== ADMIN_ID) return;
  const taskId = parseInt(ctx.match[1]);

  const taskRes = await pool.query('SELECT * FROM tasks WHERE id=$1', [taskId]);
  if (!taskRes.rows.length || taskRes.rows[0].status !== 'pending_verification') {
    return ctx.reply('Task not found or already processed.');
  }

  const task = taskRes.rows[0];
  await pool.query(`UPDATE tasks SET status='completed' WHERE id=$1`, [taskId]);
  await pool.query(`UPDATE users SET balance=balance+10 WHERE telegram_id=$1`, [task.assigned_to]);

  const userRes = await pool.query('SELECT * FROM users WHERE telegram_id=$1', [task.assigned_to]);
  await ctx.reply(`✅ Task ${taskId} confirmed. 10 birr added to ${userRes.rows[0].name}.`);
  await bot.telegram.sendMessage(task.assigned_to,
    '🎉 Your task has been verified!\n+10 birr added to your balance.\n\nPress 📋 Get Task to get a new one!'
  );

  // Check referral reward for whoever invited this worker
  if (userRes.rows[0].invited_by) {
    await checkReferralReward(userRes.rows[0].invited_by);
  }
});

// --- /reject ---
bot.hears(/^\/reject_(\d+)$/, async (ctx) => {
  if (ctx.from.id !== ADMIN_ID) return;
  const taskId = parseInt(ctx.match[1]);

  const taskRes = await pool.query('SELECT * FROM tasks WHERE id=$1', [taskId]);
  if (!taskRes.rows.length) return ctx.reply('Task not found.');

  const task = taskRes.rows[0];
  await pool.query(`
    UPDATE tasks SET status='available', assigned_to=NULL,
    assigned_at=NULL, expires_at=NULL, completed_at=NULL WHERE id=$1
  `, [taskId]);

  await ctx.reply(`❌ Task ${taskId} rejected. Returned to pool.`);
  await bot.telegram.sendMessage(task.assigned_to,
    '❌ Your task was not verified. Please try again carefully.\nPress 📋 Get Task for a new task.'
  );
});

// --- /paid ---
bot.hears(/^\/paid_(\d+)$/, async (ctx) => {
  if (ctx.from.id !== ADMIN_ID) return;
  const workerId = parseInt(ctx.match[1]);

  const res = await pool.query(`
    UPDATE withdrawals SET status='paid', paid_at=NOW()
    WHERE user_id=$1 AND status='pending'
    RETURNING *
  `, [workerId]);

  if (!res.rows.length) return ctx.reply('No pending withdrawal found for this user.');

  const w = res.rows[0];
  await ctx.reply(`✅ Payment confirmed for user ${workerId}.`);
  await bot.telegram.sendMessage(workerId,
    `✅ Your withdrawal of ${w.amount} birr has been paid!\nCheck your ${w.payment_method} account.`
  );
});

// --- Main text handler ---
bot.on('text', async (ctx) => {
  const userId = ctx.from.id;
  const text = ctx.message.text.trim();
  ctx.session = ctx.session || {};

  // Admin waiting for task details — accepts multiple messages
  if (userId === ADMIN_ID && adminWaitingForTask) {
    const lines = text.split('\n');
    const data = {};
    lines.forEach(line => {
      if (line.includes(':')) {
        const [key, ...rest] = line.split(':');
        data[key.trim().toLowerCase()] = rest.join(':').trim();
      }
    });

    const email = data['email'];
    if (!email || !email.includes('@gmail.com')) {
      return; // Silently skip non-task messages while waiting
    }

    try {
      const result = await pool.query(`
        INSERT INTO tasks (first_name, last_name, email, password, birth_year)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (email) DO NOTHING
      `, [data['first name'] || '', data['last name'] || '', email, data['password'] || '', data['year of birth'] || '']);
      if (result.rowCount > 0) {
        return ctx.reply(`✅ Task added: ${email}`);
      } else {
        return ctx.reply(`⚠️ Already exists: ${email}`);
      }
    } catch (e) {
      return ctx.reply(`❌ Error: ${e.message}`);
    }
  }

  // Registration flow
  if (ctx.session.step === 'reg_name') {
    ctx.session.name = text;
    ctx.session.step = 'reg_payment_method';
    return ctx.reply('Choose your payment method:', {
      reply_markup: {
        keyboard: [['CBE Birr', 'Telebirr']],
        one_time_keyboard: true,
        resize_keyboard: true
      }
    });
  }

  if (ctx.session.step === 'reg_payment_method') {
    if (!['CBE Birr', 'Telebirr'].includes(text)) {
      return ctx.reply('Please choose CBE Birr or Telebirr.');
    }
    ctx.session.payment_method = text;
    ctx.session.step = 'reg_payment_number';
    const label = text === 'CBE Birr' ? 'CBE account number' : 'Telebirr phone number';
    return ctx.reply(`Enter your ${label}:`, { reply_markup: { remove_keyboard: true } });
  }

  if (ctx.session.step === 'reg_payment_number') {
    const name = ctx.session.name;
    const method = ctx.session.payment_method;
    const number = text;
    const invitedBy = ctx.session.invited_by || null;

    await pool.query(`
      INSERT INTO users (telegram_id, name, payment_method, payment_number, invited_by)
      VALUES ($1, $2, $3, $4, $5)
      ON CONFLICT (telegram_id) DO NOTHING
    `, [userId, name, method, number, invitedBy]);

    ctx.session = {};
    await ctx.reply(
      `✅ Registered!\n\nName: ${name}\nPayment: ${method} — ${number}\n\nYou can now get tasks!`,
      keyboard(userId)
    );

    // Send logout video
    try {
      await ctx.replyWithVideo('AAMCAgADGQEDe7d4aobfFy1yahVPwF4ctc_2Hj-PzewAAmlrAAK-z1lKRrjAwx7HRCABAAdtAAM9BA', {
        caption: '⚠️ Important: After creating the Gmail account, make sure to LOG OUT from your device immediately!'
      });
    } catch (e) {
      await ctx.reply('⚠️ Important: After creating the Gmail account, make sure to LOG OUT from your device immediately!');
    }

    if (userId !== ADMIN_ID) {
      await bot.telegram.sendMessage(ADMIN_ID,
        `🆕 New worker registered!\nName: ${name}\nPayment: ${method} — ${number}\nID: ${userId}${invitedBy ? `\nInvited by: ${invitedBy}` : ''}`
      );
    }
    return;
  }

  // Withdrawal flow
  if (ctx.session.step === 'withdraw_amount') {
    const amount = parseInt(text);
    if (isNaN(amount)) return ctx.reply('Please enter a valid number.');

    const userRes = await pool.query('SELECT * FROM users WHERE telegram_id=$1', [userId]);
    const user = userRes.rows[0];

    if (amount < 10) return ctx.reply('Minimum withdrawal is 10 birr.');
    if (amount > user.balance) return ctx.reply(`❌ Insufficient balance. Your balance: ${user.balance} birr.`);

    await pool.query('UPDATE users SET balance=balance-$1 WHERE telegram_id=$2', [amount, userId]);
    await pool.query(`
      INSERT INTO withdrawals (user_id, amount, payment_method, payment_number)
      VALUES ($1, $2, $3, $4)
    `, [userId, amount, user.payment_method, user.payment_number]);

    ctx.session = {};
    await ctx.reply(
      `✅ Withdrawal request submitted!\nAmount: ${amount} birr\nPayment: ${user.payment_method} — ${user.payment_number}\n\nYou'll be notified once paid.`,
      keyboard(userId)
    );

    await bot.telegram.sendMessage(ADMIN_ID,
      `💸 Withdrawal Request!\n\n👤 Worker: ${user.name} (ID: ${userId})\n💰 Amount: ${amount} birr\n💳 ${user.payment_method}: ${user.payment_number}\n\nTo confirm payment: /paid_${userId}`
    );
    return;
  }

  // Menu buttons
  if (text === '📋 Get Task') {
    const userRes = await pool.query('SELECT * FROM users WHERE telegram_id=$1', [userId]);
    if (!userRes.rows.length) return ctx.reply('Please register first with /start');

    const activeRes = await pool.query(`SELECT * FROM tasks WHERE assigned_to=$1 AND status='assigned'`, [userId]);
    if (activeRes.rows.length > 0) {
      const t = activeRes.rows[0];
      const mins = Math.max(0, Math.floor((new Date(t.expires_at) - new Date()) / 60000));
      return ctx.replyWithMarkdownV2(
        formatTask(t) + `\n\n⏳ Time remaining: ${mins} minutes`
      );
    }

    const taskRes = await pool.query(`
      UPDATE tasks SET status='assigned', assigned_to=$1,
      assigned_at=NOW(), expires_at=NOW() + INTERVAL '2 hours'
      WHERE id = (SELECT id FROM tasks WHERE status='available' ORDER BY created_at ASC LIMIT 1)
      RETURNING *
    `, [userId]);

    if (!taskRes.rows.length) return ctx.reply('😔 No tasks available right now. Please check back in a few minutes.');

    return ctx.replyWithMarkdownV2(formatTask(taskRes.rows[0]));
  }

  if (text === '✅ Done') {
    const taskRes = await pool.query(`SELECT * FROM tasks WHERE assigned_to=$1 AND status='assigned'`, [userId]);
    if (!taskRes.rows.length) return ctx.reply('You have no active task to mark as done.');

    const task = taskRes.rows[0];
    await pool.query(`UPDATE tasks SET status='pending_verification', completed_at=NOW() WHERE id=$1`, [task.id]);

    const userRes = await pool.query('SELECT * FROM users WHERE telegram_id=$1', [userId]);
    const user = userRes.rows[0];

    await ctx.reply('✅ Task submitted! Waiting for verification.\nYou\'ll be notified once confirmed.');
    await bot.telegram.sendMessage(ADMIN_ID,
      `🔔 Task completed — needs verification!\n\n👤 Worker: ${user.name} (ID: ${userId})\n📧 Email: ${task.email}\nTask ID: ${task.id}\n\nTo confirm: /confirm_${task.id}\nTo reject: /reject_${task.id}`
    );
    return;
  }

  if (text === '💰 My Balance') {
    const userRes = await pool.query('SELECT * FROM users WHERE telegram_id=$1', [userId]);
    if (!userRes.rows.length) return ctx.reply('Please register first with /start');
    return ctx.reply(`💰 Your Balance: ${userRes.rows[0].balance} birr\n\nPress 💸 Withdraw to request a payout.`);
  }

  if (text === '💸 Withdraw') {
    const userRes = await pool.query('SELECT * FROM users WHERE telegram_id=$1', [userId]);
    if (!userRes.rows.length) return ctx.reply('Please register first with /start');
    if (userRes.rows[0].balance < 10) {
      return ctx.reply(`❌ Minimum withdrawal is 10 birr.\nYour balance: ${userRes.rows[0].balance} birr.`);
    }
    ctx.session.step = 'withdraw_amount';
    return ctx.reply(`Your balance: ${userRes.rows[0].balance} birr.\nHow much do you want to withdraw? (minimum 10 birr)`);
  }

  if (text === '📊 My Stats') {
    const userRes = await pool.query('SELECT * FROM users WHERE telegram_id=$1', [userId]);
    if (!userRes.rows.length) return ctx.reply('Please register first with /start');
    const completed = await pool.query(`SELECT COUNT(*) FROM tasks WHERE assigned_to=$1 AND status='completed'`, [userId]);
    const pending = await pool.query(`SELECT COUNT(*) FROM tasks WHERE assigned_to=$1 AND status='pending_verification'`, [userId]);
    return ctx.reply(
      `📊 Your Stats\n\n✅ Completed tasks: ${completed.rows[0].count}\n⏳ Pending verification: ${pending.rows[0].count}\n💰 Current balance: ${userRes.rows[0].balance} birr`
    );
  }

  if (text === '👥 My Invites') {
    const invitees = await pool.query('SELECT * FROM users WHERE invited_by=$1', [userId]);
    const inviteeIds = invitees.rows.map(r => r.telegram_id);
    let completedCount = 0;
    if (inviteeIds.length > 0) {
      const comp = await pool.query(`
        SELECT COUNT(DISTINCT assigned_to) FROM tasks
        WHERE status='completed' AND assigned_to = ANY($1)
      `, [inviteeIds]);
      completedCount = parseInt(comp.rows[0].count);
    }
    const link = getInviteLink(userId);
    const rewarded = await pool.query('SELECT id FROM referral_rewards WHERE referrer_id=$1', [userId]);

    return ctx.reply(
      `👥 My Invites\n\n` +
      `📨 Total invited: ${invitees.rows.length}/10\n` +
      `✅ Invitees completed tasks: ${completedCount}/5\n` +
      `🎁 Reward (50 birr): ${rewarded.rows.length > 0 ? 'Received ✅' : 'Not yet'}\n\n` +
      `🔗 Your invite link:\n${link}\n\n` +
      `Invite 10 workers and 5 of them must complete at least 1 task to get 50 birr!`
    );
  }

  if (text === '❓ Help') {
    return ctx.reply(
      `❓ Help\n\n` +
      `📋 *Get Task* — Get a Gmail account to create\n` +
      `✅ *Done* — Submit after creating the account\n` +
      `💰 *My Balance* — Check your earnings\n` +
      `💸 *Withdraw* — Request a payout (min 10 birr)\n` +
      `📊 *My Stats* — See your completed tasks\n` +
      `👥 *My Invites* — Your referral link and progress\n\n` +
      `⚠️ *Important — How to log out after creating Gmail:*\n\n` +
      `1. Open Gmail app on your phone\n` +
      `2. Tap your profile photo (top right)\n` +
      `3. Tap *Manage accounts on this device*\n` +
      `4. Select the new account\n` +
      `5. Tap *Remove account*\n` +
      `6. Confirm removal\n\n` +
      `You must log out immediately after creating the account, otherwise your task will not be counted.`
    );
  }

  if (text === '👥 All Workers' && userId === ADMIN_ID) {
    const res = await pool.query('SELECT * FROM users ORDER BY registered_at DESC');
    if (!res.rows.length) return ctx.reply('No workers registered yet.');
    let msg = '👥 All Workers:\n\n';
    res.rows.forEach(w => {
      msg += `👤 ${w.name} (ID: ${w.telegram_id})\n💳 ${w.payment_method}: ${w.payment_number}\n💰 Balance: ${w.balance} birr\n\n`;
    });
    return ctx.reply(msg);
  }

  if (text === '📦 All Tasks' && userId === ADMIN_ID) {
    const available = await pool.query(`SELECT COUNT(*) FROM tasks WHERE status='available'`);
    const assigned = await pool.query(`SELECT COUNT(*) FROM tasks WHERE status='assigned'`);
    const pending = await pool.query(`SELECT COUNT(*) FROM tasks WHERE status='pending_verification'`);
    const completed = await pool.query(`SELECT COUNT(*) FROM tasks WHERE status='completed'`);
    return ctx.reply(
      `📦 Task Summary:\n\n🟢 Available: ${available.rows[0].count}\n🔵 Assigned: ${assigned.rows[0].count}\n🟡 Pending verification: ${pending.rows[0].count}\n✅ Completed: ${completed.rows[0].count}`
    );
  }
});

const PORT = process.env.PORT || 3000;
http.createServer((req, res) => {
  res.writeHead(200);
  res.end('OK - bot running');
}).listen(PORT, () => console.log(`[keep-alive] Ping server on port ${PORT}`));

initDB().then(() => {
  bot.launch();
  console.log('Bot started.');
});

process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));

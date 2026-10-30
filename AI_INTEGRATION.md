# AI Assistant + Sir chat monitoring (Beyonder → Math Class)

একটাই Flask app, একটাই PythonAnywhere site। আলাদা Vercel/Beyonder লাগে না।

## কী যোগ হয়েছে
| ফাইল | কাজ |
|---|---|
| `backend/ai/provider.py` | Gemini/Groq call (server-side), fallback, timeout |
| `backend/ai/context.py` | DB থেকে যাচাই-করা results/ভুল উত্তর, test-mode নির্ধারণ |
| `backend/routes/ai_api.py` | ছাত্রের API: `/api/v1/ai/{status,chat,conversations,...}` |
| `backend/routes/ai_admin.py` | Sir-এর API + পেজ `/teacher/ai-chats` |
| `backend/migrations.py` | নতুন `006_ai_chat` (শুধু নতুন ২টি table) |
| `static/ai-widget.js/.css` | নিচের ডানদিকের গোল button ও chat window |
| `templates/teacher_ai_chats.html` | Sir-এর monitoring UI |
| `templates/base.html` | ছাত্রের পেজে widget include + Sir-এর nav-এ "AI Chats" |
| `tests/test_ai.py` | ২৪টি নতুন test; `tests/test_backend.py`-এ শুধু migration তালিকা হালনাগাদ |

## .env (PythonAnywhere-এর project folder-এ `.env`)
```
GEMINI_API_KEY=...        # default provider
GROQ_API_KEY=...          # fallback
```
Gemini fail/timeout/429/5xx করলে নিজে Groq (`openai/gpt-oss-120b`) চেষ্টা করে। দুটোই fail করলে ছাত্র নিরাপদ error ও Retry দেখে, কিছু save হয় না। Key কখনো browser/GitHub-এ যায় না।

## Database migration
- App চালু হলে `006_ai_chat` নিজে চলে (স্বয়ংক্রিয় backup নেওয়া হয় **না**)। কোনো পুরোনো table/row বদলায় না।
- হাতে backup নিতে: `python -m backend.migrations backup`
- হাতে চালাতে: `python -m backend.migrations status` / `migrate`।
- ফেরত নিতে: `python -m backend.migrations rollback 006_ai_chat` (শুধু AI table মোছে)।
- মনে রাখবেন: কোনো ছাত্রকে delete করলে তার AI chat-ও মুছে যায় (results-এর মতোই)।

## PythonAnywhere deploy
1. **আপনার live `database.db` ও `uploads/` ওভাররাইট করবেন না** (zip-এ এগুলো দেওয়া নেই)।
2. Zip-এর ফাইলগুলো project folder-এ আপলোড/ওভাররাইট করুন।
3. Bash console: `pip install --user -r requirements.txt` (requests সাধারণত আগেই আছে)।
4. `.env`-এ উপরের key বসান।
5. Web ট্যাবে **Reload**।
6. Free account-এ `generativelanguage.googleapis.com` ও `api.groq.com` allowed (PythonAnywhere allowlist-এ আছে)।

## ছাত্রের জন্য
- Login করা ছাত্রের প্রতিটি পেজে নিচের ডানদিকে 💬 button। Teacher/"viewing as student" অবস্থায় AI বন্ধ।
- Test খোলা ও submit-না-করা অবস্থায় (সময়সীমা + ২ মিনিট grace) AI "test mode"-এ থাকে: concept বোঝায়, ওই test-এর প্রশ্ন সমাধান করে না। এটা prompt-ভিত্তিক নিয়ম, ১০০% গ্যারান্টি নয়; সম্পূর্ণ বন্ধ চাইলে বলুন।
- Chat window খোলা/বন্ধ করলে test-এর বাছাই করা উত্তর হারায় না (browser-এ যাচাই করা হয়েছে)।

## Sir-এর ব্যবহার
Sir login → উপরের **AI Chats** → ছাত্র খুঁজুন/বাছুন → conversation → পুরো message (sender, সময়, পুরোনো→নতুন)। "Search all messages" দিয়ে সব chat-এ শব্দ খোঁজা যায়। শুধু নাম/শ্রেণি/রোল দেখা যায়; email, মোবাইল দেখানো হয় না। Chat-এর সঙ্গে test থাকলে test-এর নাম ও (জমা দিলে) নম্বর দেখা যায়। ছাত্ররা chat-এ Sir দেখতে পারেন — এই কথা widget-এ লেখা আছে।


## Multi-AI: সব কিছু `.env` থেকে (key বসালে চালু, না বসালে বন্ধ)
- **উত্তর দেওয়া:** `.env`-এ যে AI-এর key বসাবে সেটাই উত্তর দেয়। key খালি থাকলে বা ফেল করলে পরের চালু AI উত্তর দেয়, কোনো error হয় না।
- **বোতামে দেখানো:** যে AI-এর নাম `.env`-এ লেখা (`AI_NAME_...`) সেটা ছাত্রদের বোতামে সবসময় দেখায়, কাজ করুক বা না করুক।
- **নাম:** `AI_NAME_<আসল AI>=ছাত্রদের নাম`, যেমন `AI_NAME_GEMINI=Anubhab Dutta`, `AI_NAME_OPENAI=Rahul Sen`। আসল নাম ছাত্ররা দেখে না।
- **একই নাম দুই AI-কে** দিলে একটাই বোতাম; প্রথমটা না চললে দ্বিতীয়টা উত্তর দেয়।
- **নাম খালি:** gemini/gpt-oss/qwen/groq পুরোনো নাম পায়, বাকিরা "AI 6" ধরনের নিরপেক্ষ নাম।
- **Qwen:** `QWEN_API_KEY` থাকলে সরাসরি Alibaba থেকে (`qwen-plus`); না থাকলে `GROQ_API_KEY` দিয়ে Groq-এ চলে।
- **Fallback ও ক্রম:** একটা AI fail করলে পরেরটা নিজে চলে। `AI_PROVIDER_ORDER=openai,gemini` দিলে শুধু এই দুটো, এই ক্রমে।
- **Model বদলানো:** `OPENAI_MODEL=...` ইত্যাদি। Default model নামগুলো আন্দাজ-করা নিরাপদ মান; সার্ভিসের ডকুমেন্টে যাচাই করে নিও।
- পুরো তালিকা ও ব্যাখ্যা `.env.example`-এ আছে। বদলানোর পর PythonAnywhere-এ Reload দাও।

## ছাত্রের নিজের History
AI উইজেটের উপরে **History** বোতাম: ছাত্র তার আগের সব চ্যাট (শিরোনাম ও সময়সহ) দেখতে পায়, যেকোনোটা খুলে পড়তে ও সেখান থেকে আবার চালিয়ে যেতে পারে। ব্রাউজার বন্ধ করলেও হারায় না, কারণ সব সার্ভারে সেভ থাকে। একজন ছাত্র কেবল নিজের চ্যাটই দেখে।

## কোন AI ফেল করছে কীভাবে জানবে
Sir হিসেবে লগইন করে **AI Chats** পেজে যাও, উপরে **Test all AIs** চাপো। প্রতিটা AI-কে একটা ছোট প্রশ্ন পাঠিয়ে দেখায় ✅ চলছে / ❌ ফেল (কারণসহ, যেমন key ভুল, balance নেই, model নাম ভুল, বা হোস্টিং নেটওয়ার্ক ব্লক করছে) / ⚪ key নেই। Key কখনো দেখায় না।
PythonAnywhere-এর ফ্রি অ্যাকাউন্টে শুধু allowlist-এর সাইটে যাওয়া যায়; কোনো AI-এর ডোমেইন allowlist-এ না থাকলে ওখানে "Blocked by the hosting network" দেখাবে।

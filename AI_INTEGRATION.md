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
- App চালু হলে `006_ai_chat` নিজে চলে (আগে স্বয়ংক্রিয় backup নেয়)। কোনো পুরোনো table/row বদলায় না।
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


## নতুন AI যোগ করা ও ছাত্রদের দেখানো নাম বদলানো (শুধু `.env` দিয়ে)
কোড বদলাতে হয় না। `.env`-এ key বসান, তারপর Web ট্যাবে **Reload**।

**১. Key:** `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, `XAI_API_KEY`, `TOGETHER_API_KEY`, `CEREBRAS_API_KEY`, `PERPLEXITY_API_KEY`। এছাড়া যেকোনো OpenAI-compatible সার্ভিসের জন্য `CUSTOM_AI_BASE_URL` + `CUSTOM_AI_API_KEY` + `CUSTOM_AI_MODEL`।

**২. ছাত্ররা যে নাম দেখবে — `AI_NAME_<আসল AI>=ছাত্রদের নাম`:**
```
AI_NAME_GEMINI=Anubhab Dutta
AI_NAME_GPT_OSS=Arnab Adhikari
AI_NAME_OPENAI=Rahul Sen
AI_NAME_ANTHROPIC=Priya Das
```
বাঁ দিকে আসল AI (তাই কোনটা কোন AI সবসময় বোঝা যায়), ডান দিকে যে নাম খুশি, বাংলাতেও চলবে।
- আসল AI-এর নাম: `GEMINI`, `GPT_OSS`, `QWEN`, `GROQ`, `OPENAI`, `ANTHROPIC`, `DEEPSEEK`, `MISTRAL`, `OPENROUTER`, `XAI`, `TOGETHER`, `CEREBRAS`, `PERPLEXITY`, `CUSTOM`।
- দুটো AI-কে **একই নাম** দিলে ছাত্ররা একটাই বোতাম দেখে; প্রথমটা আগে চেষ্টা হয়, না চললে দ্বিতীয়টা। (Default-এ gpt-oss, qwen, groq তিনটাই "Arnab Adhikari"।)
- নাম না দিলে ওই AI ব্যবহারই হয় না। Key না থাকলে বোতামও আসে না।
- ছাত্ররা আসল নাম (Gemini/OpenAI...) কখনো দেখে না; শুধু Sir-এর AI Chats পেজে দেখা যায়।
- বোতামের ক্রম বদলাতে: `AI_PROVIDER_ORDER=openai,gemini,gpt-oss`।

**৩. Model বদলানো:** `OPENAI_MODEL=...`, `ANTHROPIC_MODEL=...` ইত্যাদি। Default model নামগুলো আমার আন্দাজ-করা মান; সার্ভিসের ডকুমেন্টে যাচাই করে দরকারে `.env`-এ বদলে নিন।

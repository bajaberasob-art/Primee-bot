"""Central command metadata for the dashboard and command help surfaces.

This module intentionally contains data only.  It must remain safe to import
without loading Discord cogs, opening the database, or performing I/O.
"""

from __future__ import annotations

from copy import deepcopy


COMMAND_CATEGORIES = (
    "الطرد والحظر",
    "الإسكات والصوت",
    "إدارة الشات",
    "Blacklist القنوات",
    "الأمان — السجن",
    "التحذيرات والإدارة",
    "أوامر الأعضاء — معلومات",
    "أوامر الأعضاء — أدوات",
    "إدارة القنوات",
    "أوامر متقدمة",
    "السجل والملاحظات",
    "إحصائيات وتراجع",
)

AUTO_DELETE_PRESETS = (0, 5, 10, 30, 60, 300)
RESPONSE_STYLES = ("default", "embed", "compact", "silent")


def _command(
    key: str,
    display_name: str,
    category: str,
    required_permission: str,
    aliases: list[str],
    syntax: str,
    example: str,
    description: str,
) -> dict:
    return {
        "key": key,
        "display_name": display_name,
        "category": category,
        "required_permission": required_permission,
        "default_aliases": list(aliases),
        "syntax": syntax,
        "example": example,
        "description": description,
    }


_COMMAND_SPECS = {
    # الطرد والحظر
    "kick": ("طرد (Kick)", "الطرد والحظر", "kick_members", ["k", "طرد", "زوووط", "دزمها"], "!kick @عضو [السبب]", "!kick @أحمد السلوك السيء", "طرد عضو من السيرفر مع تسجيل السبب وإبلاغ العضو بالإجراء."),
    "ban": ("حظر (Ban)", "الطرد والحظر", "ban_members", ["b", "حظر"], "!ban @عضو [السبب]", "!ban @أحمد إساءة متكررة", "حظر عضو من السيرفر ومنعه من العودة وفق صلاحيات Discord."),
    "tempban": ("حظر مؤقت (Tempban)", "الطرد والحظر", "ban_members", ["tb", "حظر_مؤقت"], "!tempban @عضو [المدة] [السبب]", "!tempban @أحمد 7d تحذير أخير", "حظر عضو لمدة محددة ثم رفع الحظر تلقائياً عند انتهاء المدة."),
    "unban": ("فك الحظر (Unban)", "الطرد والحظر", "ban_members", ["ub", "فك_الحظر"], "!unban [معرف العضو]", "!unban 123456789012345678", "رفع الحظر عن عضو باستخدام معرف Discord الخاص به."),
    "softban": ("حظر ناعم (Softban)", "الطرد والحظر", "ban_members", ["soft"], "!softban @عضو [السبب]", "!softban @أحمد تنظيف الرسائل", "طرد العضو وحذف رسائله الحديثة ثم السماح له بالعودة."),
    "massban": ("حظر جماعي (Massban)", "الطرد والحظر", "ban_members", ["mb"], "!massban [الأعضاء]", "!massban @عضو1 @عضو2", "تنفيذ حظر جماعي بعد التحقق من الصلاحيات والتأكيد."),
    "masskick": ("طرد جماعي (Masskick)", "الطرد والحظر", "kick_members", ["mk"], "!masskick [الأعضاء]", "!masskick @عضو1 @عضو2", "تنفيذ طرد جماعي للأعضاء المحددين بعد تأكيد العملية."),
    "massmute": ("إسكات جماعي (Massmute)", "الطرد والحظر", "moderate_members", ["mmute"], "!massmute [الأعضاء] [المدة]", "!massmute @عضو1 @عضو2 10m", "تطبيق تايم أوت على مجموعة أعضاء مع عداد نجاح وفشل."),
    "unbanall": ("فك حظر الكل (Unban All)", "الطرد والحظر", "ban_members", ["فك_حظر_الكل"], "!unbanall", "!unbanall", "فك حظر جميع الحسابات المحظورة مع حماية معدل الطلبات."),
    "clearbans": ("مسح الحظر (Clear Bans)", "الطرد والحظر", "administrator", ["مسح_الحظر"], "!clearbans", "!clearbans", "مسح قائمة الحظر بالكامل مع تسجيل المشرف المنفذ."),
    # الإسكات والصوت
    "timeout": ("تايم أوت (Timeout)", "الإسكات والصوت", "moderate_members", ["to", "تايم"], "!timeout @عضو [الدقائق] [السبب]", "!timeout @أحمد 30 إزعاج", "إسكات عضو لمدة محددة باستخدام نظام Timeout الرسمي في Discord."),
    "untimeout": ("فك التايم أوت (Untimeout)", "الإسكات والصوت", "moderate_members", ["uto"], "!untimeout @عضو", "!untimeout @أحمد", "إزالة حالة Timeout والسماح للعضو بالتحدث مجدداً."),
    "mute": ("إسكات (Mute)", "الإسكات والصوت", "moderate_members", ["m", "اسكت"], "!mute @عضو [المدة] [السبب]", "!mute @أحمد 10m إزعاج", "إسكات عضو وفق إعدادات السيرفر مع حفظ مدة الإجراء وسببه."),
    "unmute": ("فك الإسكات (Unmute)", "الإسكات والصوت", "moderate_members", ["um"], "!unmute @عضو", "!unmute @أحمد", "إلغاء إسكات العضو وإعادة صلاحية التحدث له."),
    "voice_mute": ("كتم صوتي (Voice Mute)", "الإسكات والصوت", "mute_members", ["vmute"], "!voice_mute @عضو [السبب]", "!voice_mute @أحمد ضوضاء", "كتم صوت عضو داخل القناة الصوتية الحالية."),
    "voice_unmute": ("فك كتم صوتي (Voice Unmute)", "الإسكات والصوت", "mute_members", ["vunmute"], "!voice_unmute @عضو", "!voice_unmute @أحمد", "إلغاء الكتم الصوتي عن عضو داخل القناة."),
    "voice_move": ("نقل صوتي (Voice Move)", "الإسكات والصوت", "move_members", ["vmove"], "!voice_move @عضو [القناة]", "!voice_move @أحمد غرفة-1", "نقل عضو إلى قناة صوتية يحددها المشرف."),
    "voice_disconnect": ("فصل صوتي (Voice Disconnect)", "الإسكات والصوت", "move_members", ["vdc"], "!voice_disconnect @عضو", "!voice_disconnect @أحمد", "إخراج عضو من القناة الصوتية الحالية."),
    "radio": ("إذاعة (Radio)", "الإسكات والصوت", "connect", ["راديو"], "!radio", "!radio", "تشغيل البث الصوتي المباشر داخل القناة الصوتية."),
    "stop_radio": ("إيقاف الإذاعة (Stop Radio)", "الإسكات والصوت", "connect", ["إيقاف_الراديو"], "!stop_radio", "!stop_radio", "إيقاف البث الصوتي وفصل البوت من القناة."),
    "mutedlist": ("قائمة المسكوتين (Muted List)", "الإسكات والصوت", "moderate_members", ["المسكوتين"], "!mutedlist", "!mutedlist", "عرض الأعضاء الخاضعين للتايم أوت أو الميوت الكتابي."),
    "vmute": ("كتم صوتي (VMute)", "الإسكات والصوت", "mute_members", ["كتم_صوتي"], "!vmute @عضو [السبب]", "!vmute @أحمد ضوضاء", "كتم ميكروفون عضو داخل القناة الصوتية."),
    "vunmute": ("فك كتم صوتي (VUnmute)", "الإسكات والصوت", "mute_members", ["فك_كتم_صوتي"], "!vunmute @عضو", "!vunmute @أحمد", "إلغاء كتم ميكروفون عضو داخل القناة الصوتية."),
    "vkick": ("طرد صوتي (VKick)", "الإسكات والصوت", "move_members", ["طرد_صوتي"], "!vkick @عضو", "!vkick @أحمد", "فصل عضو من القناة الصوتية الحالية."),
    "deafen": ("صم صوتي (Deafen)", "الإسكات والصوت", "deafen_members", ["صمم"], "!deafen @عضو", "!deafen @أحمد", "منع عضو من سماع الصوت داخل القناة."),
    "undeafen": ("فك الصم (Undeafen)", "الإسكات والصوت", "deafen_members", ["فك_الصمم"], "!undeafen @عضو", "!undeafen @أحمد", "إلغاء صمم عضو داخل القناة الصوتية."),
    "vban": ("حظر صوتي (VBan)", "الإسكات والصوت", "move_members", ["حظر_صوتي"], "!vban @عضو", "!vban @أحمد", "حظر عضو من دخول القنوات الصوتية وفصله فوراً."),
    "vunban": ("فك الحظر الصوتي (VUnban)", "الإسكات والصوت", "move_members", ["فك_حظر_صوتي"], "!vunban @عضو", "!vunban @أحمد", "إزالة الحظر الصوتي عن عضو."),
    "vmove": ("نقل صوتي (VMove)", "الإسكات والصوت", "move_members", ["نقل_صوتي"], "!vmove @عضو [القناة]", "!vmove @أحمد غرفة-1", "نقل عضو من قناته الصوتية إلى قناة أخرى."),
    "moveall": ("نقل الكل صوتياً (Move All)", "الإسكات والصوت", "move_members", ["نقل_الكل"], "!moveall [القناة]", "!moveall غرفة-2", "نقل جميع أعضاء قناة صوتية إلى قناة أخرى."),
    "disconnectall": ("قطع الكل (Disconnect All)", "الإسكات والصوت", "move_members", ["قطع_الكل"], "!disconnectall", "!disconnectall", "فصل جميع الأعضاء من القناة الصوتية الحالية."),
    "vlock": ("قفل روم صوتي (Voice Lock)", "الإسكات والصوت", "manage_channels", ["قفل_صوتي"], "!vlock", "!vlock", "منع الأعضاء من الانضمام إلى القناة الصوتية الحالية."),
    "vunlock": ("فتح روم صوتي (Voice Unlock)", "الإسكات والصوت", "manage_channels", ["فتح_صوتي"], "!vunlock", "!vunlock", "إعادة السماح للأعضاء بالانضمام إلى القناة الصوتية."),
    # إدارة الشات
    "clear": ("مسح (Clear)", "إدارة الشات", "manage_messages", ["c", "مسح", "purge"], "!clear [العدد]", "!clear 50", "حذف عدد محدد من رسائل القناة مع احترام حدود Discord."),
    "clear_user": ("مسح رسائل عضو (Clear User)", "إدارة الشات", "manage_messages", ["مسح_عضو"], "!clear_user @عضو [العدد]", "!clear_user @أحمد 50", "حذف رسائل عضو محدد من القناة الحالية مع تسجيل الإجراء."),
    "cleanup": ("تنظيف القناة (Cleanup)", "إدارة الشات", "manage_messages", ["تنظيف_القناة"], "!cleanup [العدد]", "!cleanup 100", "حذف دفعة رسائل من القناة الحالية ضمن الحد الآمن."),
    "purge": ("تنظيف شامل (Purge)", "إدارة الشات", "manage_messages", ["تنظيف"], "!purge [العدد]", "!purge 100", "تنظيف دفعة كبيرة من الرسائل بعد تأكيد المشرف."),
    "say": ("رسالة رسمية (Say)", "إدارة الشات", "manage_messages", ["قل"], "!say [النص]", "!say سيتم إغلاق القناة للصيانة", "إرسال رسالة باسم البوت بعد التحقق من صلاحية إدارة الرسائل."),
    "announce": ("إعلان (Announce)", "إدارة الشات", "manage_messages", ["إعلان"], "!announce [العنوان] [النص]", "!announce تحديث السيرفر تم إطلاق الموسم الجديد", "نشر إعلان منسق في القناة الحالية."),
    "embed": ("رسالة Embed", "إدارة الشات", "manage_messages", ["إمبد"], "!embed [العنوان] | [النص]", "!embed القوانين | يرجى قراءة القوانين قبل المشاركة", "إنشاء رسالة Embed منسقة وقابلة للقراءة."),
    "react": ("تفاعل (React)", "إدارة الشات", "manage_messages", ["تفاعل"], "!react [الإيموجي]", "!react ✅", "إضافة تفاعل إلى آخر رسالة في القناة."),
    "poll": ("استطلاع (Poll)", "إدارة الشات", "manage_messages", ["تصويت", "استطلاع"], "!poll [السؤال] | [الخيارات]", "!poll أفضل لعبة؟ | Valorant | FIFA", "إنشاء استطلاع تفاعلي مع أزرار ونسب مئوية مباشرة."),
    "slowmode": ("الوضع البطيء (Slowmode)", "إدارة الشات", "manage_channels", ["بطء"], "!slowmode [الثواني]", "!slowmode 10", "تعيين الفاصل الزمني بين رسائل الأعضاء في القناة."),
    "slowmode_off": ("إلغاء الوضع البطيء (Slowmode Off)", "إدارة الشات", "manage_channels", ["إلغاء_البطء"], "!slowmode_off", "!slowmode_off", "إلغاء الوضع البطيء وإعادة القناة إلى الإرسال الطبيعي."),
    "lockdown": ("قفل الشات (Lockdown)", "إدارة الشات", "manage_channels", ["lock", "قفل"], "!lockdown [تشغيل/إيقاف]", "!lockdown true", "قفل أو فتح الكتابة في القناة للأعضاء غير المشرفين."),
    "lock": ("قفل القناة (Lock)", "إدارة الشات", "manage_channels", ["قفل"], "!lock [القناة]", "!lock #الإعلانات", "منع الأعضاء من الكتابة في قناة محددة."),
    "unlock": ("فتح القناة (Unlock)", "إدارة الشات", "manage_channels", ["فتح"], "!unlock [القناة]", "!unlock #الإعلانات", "إعادة السماح للأعضاء بالكتابة في القناة."),
    "thread_lock": ("قفل المحادثة (Thread Lock)", "إدارة الشات", "manage_threads", ["قفل_ثريد"], "!thread_lock", "!thread_lock", "قفل المحادثة الفرعية الحالية ومنع الردود الجديدة."),
    "thread_unlock": ("فتح المحادثة (Thread Unlock)", "إدارة الشات", "manage_threads", ["فتح_ثريد"], "!thread_unlock", "!thread_unlock", "فتح المحادثة الفرعية الحالية للسماح بالردود."),
    "nuke": ("تطهير القناة (Nuke)", "إدارة الشات", "manage_channels", ["تطهير"], "!nuke [السبب]", "!nuke تنظيف مخالفات", "استنساخ القناة بإعداداتها ثم حذف النسخة القديمة لتطهير محتواها."),
    "lockall": ("قفل القنوات (Lock All)", "إدارة الشات", "manage_channels", ["قفل_الكل"], "!lockall", "!lockall", "قفل الكتابة في القنوات العامة غير الإدارية."),
    "unlockall": ("فتح القنوات (Unlock All)", "إدارة الشات", "manage_channels", ["فتح_الكل"], "!unlockall", "!unlockall", "إعادة فتح الكتابة في القنوات العامة غير الإدارية."),
    "hide": ("إخفاء القناة (Hide)", "إدارة الشات", "manage_channels", ["إخفاء"], "!hide", "!hide", "إخفاء القناة الحالية عن الأعضاء غير الإداريين."),
    "show": ("إظهار القناة (Show)", "إدارة الشات", "manage_channels", ["إظهار"], "!show", "!show", "إظهار القناة الحالية للأعضاء."),
    "hideall": ("إخفاء القنوات (Hide All)", "إدارة الشات", "manage_channels", ["إخفاء_الكل"], "!hideall", "!hideall", "إخفاء القنوات العامة عن الأعضاء غير الإداريين."),
    "showall": ("إظهار القنوات (Show All)", "إدارة الشات", "manage_channels", ["إظهار_الكل"], "!showall", "!showall", "إظهار القنوات العامة للأعضاء."),
    "emergency": ("وضع الطوارئ (Emergency)", "إدارة الشات", "administrator", ["panic", "طوارئ"], "!emergency [السبب]", "!emergency حماية عاجلة", "قفل وإخفاء القنوات العامة وتفعيل أعلى مستوى تحقق متاح."),
    "clean_commands": ("تنظيف الأوامر (Clean Commands)", "إدارة الشات", "manage_messages", ["تنظيف_الأوامر"], "!clean_commands [العدد]", "!clean_commands 100", "حذف رسائل الأوامر ورسائل البوتات من القناة."),
    "clean_bots": ("تنظيف البوتات (Clean Bots)", "إدارة الشات", "manage_messages", ["تنظيف_البوتات"], "!clean_bots [العدد]", "!clean_bots 100", "حذف رسائل حسابات البوتات من القناة."),
    "delete_after": ("حذف ما بعد الرسالة (Delete After)", "إدارة الشات", "manage_messages", ["حذف_بعد"], "!delete_after [رابط الرسالة]", "!delete_after https://discord.com/channels/...", "حذف الرسائل الواقعة بعد رسالة مرجعية محددة."),
    "delete_before": ("حذف ما قبل الرسالة (Delete Before)", "إدارة الشات", "manage_messages", ["حذف_قبل"], "!delete_before [رابط الرسالة]", "!delete_before https://discord.com/channels/...", "حذف الرسائل الواقعة قبل رسالة مرجعية محددة."),
    "block_write": ("منع كتابة عضو (Block Write)", "إدارة الشات", "manage_channels", ["منع_الكتابة"], "!block_write @عضو", "!block_write @أحمد", "منع عضو محدد من الكتابة في القناة الحالية مع حفظ القيد."),
    "unblock_write": ("رفع منع الكتابة (Unblock Write)", "إدارة الشات", "manage_channels", ["رفع_منع_الكتابة"], "!unblock_write @عضو", "!unblock_write @أحمد", "رفع منع الكتابة عن عضو محدد وإزالة القيد المحفوظ."),
    "hide_member": ("إخفاء القناة عن عضو (Hide Member)", "إدارة الشات", "manage_channels", ["إخفاء_عضو"], "!hide_member @عضو", "!hide_member @أحمد", "إخفاء القناة الحالية عن عضو محدد مع حفظ القيد."),
    "show_member": ("إظهار القناة لعضو (Show Member)", "إدارة الشات", "manage_channels", ["إظهار_عضو"], "!show_member @عضو", "!show_member @أحمد", "إظهار القناة لعضو محدد وإزالة قيد الإخفاء."),
    "open_chat_member": ("فتح الشات لعضو (Open Chat Member)", "إدارة الشات", "manage_channels", ["فتح_شات_عضو"], "!open_chat_member @عضو", "!open_chat_member @أحمد", "منح عضو محدد صلاحيات رؤية وكتابة استثنائية في القناة."),
    "remove_chat_member": ("إزالة صلاحيات عضو (Remove Chat Member)", "إدارة الشات", "manage_channels", ["إزالة_عضو_من_الشات"], "!remove_chat_member @عضو", "!remove_chat_member @أحمد", "إزالة صلاحيات العضو الاستثنائية وإعادته إلى صلاحيات القناة الأصلية."),
    # Blacklist القنوات
    "blacklist": ("حظر القنوات (Blacklist)", "Blacklist القنوات", "manage_channels", ["بلوك", "بلاكليست"], "!blacklist [القناة]", "!blacklist #الدردشة", "إضافة قناة إلى قائمة القنوات المحظورة من استخدام أوامر محددة."),
    "unblacklist": ("إزالة حظر القناة (Unblacklist)", "Blacklist القنوات", "manage_channels", ["فك_البلوك"], "!unblacklist [القناة]", "!unblacklist #الدردشة", "إزالة القناة من قائمة القنوات المحظورة."),
    "blacklist_list": ("قائمة الحظر (Blacklist List)", "Blacklist القنوات", "manage_channels", ["قائمة_البلوك"], "!blacklist_list", "!blacklist_list", "عرض القنوات الموجودة حالياً في قائمة الحظر."),
    "channelinfo": ("معلومات القناة (Channel Info)", "Blacklist القنوات", "manage_channels", ["معلومات_القناة"], "!channelinfo [القناة]", "!channelinfo #الدردشة", "عرض معرف القناة وصلاحياتها وإعداداتها الأساسية."),
    # الأمان — السجن
    "jail": ("السجن (Jail)", "الأمان — السجن", "administrator", ["سجن"], "!jail @عضو [السبب]", "!jail @أحمد تخريب", "نقل العضو إلى نظام السجن ومنعه من الوصول إلى القنوات العامة."),
    "unjail": ("فك السجن (Unjail)", "الأمان — السجن", "administrator", ["فك_السجن"], "!unjail @عضو", "!unjail @أحمد", "إخراج العضو من السجن وإعادة صلاحياته السابقة."),
    "solo_jail": ("السجن الفردي (Solo Jail)", "الأمان — السجن", "administrator", ["سجن_فردي"], "!solo_jail @عضو [السبب]", "!solo_jail @أحمد مخالفة", "إنشاء قناة خاصة ومعزولة للعضو مع حفظ رتبته السابقة."),
    "jail_setup": ("تهيئة السجن (Jail Setup)", "الأمان — السجن", "administrator", ["تهيئة_السجن"], "!jail_setup", "!jail_setup", "تهيئة رتبة وقناة السجن في السيرفر بشكل آمن."),
    "setup_captcha": ("بوابة التحقق (Captcha)", "الأمان — السجن", "administrator", ["كابتشا", "تحقق"], "!setup_captcha", "!setup_captcha", "تثبيت بوابة تحقق بشرية للأعضاء الجدد."),
    "anti_nuke": ("الحماية من التخريب (Anti-Nuke)", "الأمان — السجن", "administrator", ["حماية"], "!anti_nuke [تشغيل/إيقاف]", "!anti_nuke true", "تفعيل أو تعطيل طبقة الحماية من عمليات التخريب الجماعي."),
    "security_status": ("حالة الأمان (Security Status)", "الأمان — السجن", "administrator", ["حالة_الأمان"], "!security_status", "!security_status", "عرض حالة طبقات الحماية والحوادث الأمنية الأخيرة."),
    "audit": ("تدقيق الأمان (Audit)", "الأمان — السجن", "view_audit_log", ["تدقيق"], "!audit [العدد]", "!audit 20", "عرض آخر أحداث سجل Discord مع تنسيق قابل للمراجعة."),
    # التحذيرات والإدارة
    "warn": ("تحذير (Warn)", "التحذيرات والإدارة", "kick_members", ["w", "تحذير", "عيب"], "!warn @عضو [السبب]", "!warn @أحمد لغة غير لائقة", "إضافة تحذير موثق إلى سجل العضو وإبلاغه بالسبب."),
    "warnings": ("سجل التحذيرات (Warnings)", "التحذيرات والإدارة", "kick_members", ["تحذيرات", "سجل"], "!warnings @عضو", "!warnings @أحمد", "عرض أرشيف تحذيرات عضو مع أرقام السجلات."),
    "unwarn": ("إلغاء تحذير (Unwarn)", "التحذيرات والإدارة", "kick_members", ["حذف_تحذير"], "!unwarn [رقم السجل]", "!unwarn 42", "إلغاء تحذير محفوظ باستخدام رقم السجل."),
    "delwarn": ("حذف تحذير (Delete Warning)", "التحذيرات والإدارة", "kick_members", ["حذف_تحذير_جديد"], "!delwarn [رقم السجل]", "!delwarn 42", "حذف تحذير من سجل التحذيرات الإداري الجديد."),
    "clearwarns": ("مسح التحذيرات (Clear Warnings)", "التحذيرات والإدارة", "kick_members", ["مسح_التحذيرات"], "!clearwarns @عضو", "!clearwarns @أحمد", "مسح جميع التحذيرات المحفوظة لعضو واحد."),
    "case": ("ملف الإجراء (Case)", "التحذيرات والإدارة", "kick_members", ["حالة"], "!case [رقم السجل]", "!case 42", "عرض تفاصيل إجراء إداري واحد من سجل السيرفر."),
    "modlogs": ("سجل الإدارة (Modlogs)", "التحذيرات والإدارة", "view_audit_log", ["سجل_الإدارة"], "!modlogs [العدد]", "!modlogs 25", "عرض الإجراءات الإدارية الأخيرة للمتابعة والمراجعة."),
    "note": ("ملاحظة عضو (Note)", "التحذيرات والإدارة", "manage_messages", ["ملاحظة"], "!note @عضو [النص]", "!note @أحمد يحتاج متابعة", "حفظ ملاحظة داخلية عن عضو دون إرسالها له."),
    "notes": ("ملاحظات العضو (Notes)", "التحذيرات والإدارة", "manage_messages", ["ملاحظات"], "!notes @عضو", "!notes @أحمد", "عرض الملاحظات الداخلية المحفوظة لعضو."),
    "delnote": ("حذف ملاحظة (Delete Note)", "التحذيرات والإدارة", "manage_messages", ["حذف_ملاحظة"], "!delnote [رقم الملاحظة]", "!delnote 12", "حذف ملاحظة إدارية سرية باستخدام معرفها."),
    "setnick": ("تغيير لقب (Set Nick)", "التحذيرات والإدارة", "manage_nicknames", ["لقب"], "!setnick @عضو [اللقب]", "!setnick @أحمد VIP", "تغيير الاسم المستعار لعضو بعد فحص ترتيب الرتب."),
    "summon": ("استدعاء (Summon)", "التحذيرات والإدارة", "manage_messages", ["استدعاء", "نداء"], "!summon @عضو [الرسالة]", "!summon @أحمد راجع الإدارة", "إرسال استدعاء خاص أنيق للعضو مع رابط القناة."),
    "role": ("إدارة رتبة (Role)", "التحذيرات والإدارة", "manage_roles", ["رتبة"], "!role @عضو [الرتبة]", "!role @أحمد VIP", "إضافة رتبة إلى عضو بعد التحقق من ترتيب الرتب."),
    "addrole": ("إضافة رتبة (Add Role)", "التحذيرات والإدارة", "manage_roles", ["إضافة_رتبة"], "!addrole @عضو [الرتبة]", "!addrole @أحمد VIP", "إضافة رتبة واحدة أو أكثر إلى عضو."),
    "removerole": ("إزالة رتبة (Remove Role)", "التحذيرات والإدارة", "manage_roles", ["إزالة_رتبة"], "!removerole @عضو [الرتبة]", "!removerole @أحمد VIP", "إزالة رتبة من عضو بعد فحص صلاحيات البوت."),
    "massrole": ("رتبة جماعية (Mass Role)", "التحذيرات والإدارة", "manage_roles", ["mr"], "!massrole [الرتبة] [الأعضاء]", "!massrole VIP @أحمد @سالم", "تطبيق رتبة على مجموعة أعضاء بعد تأكيد العملية."),
    "give_role": ("إعطاء رتبة (Give Role)", "التحذيرات والإدارة", "manage_roles", ["إعطاء_رتبة"], "!give_role @عضو [الرتبة]", "!give_role @أحمد VIP", "منح رتبة لعضو بعد التحقق من صلاحيات البوت والهرمية."),
    "take_role": ("سحب رتبة (Take Role)", "التحذيرات والإدارة", "manage_roles", ["سحب_رتبة"], "!take_role @عضو [الرتبة]", "!take_role @أحمد VIP", "سحب رتبة من عضو بعد التحقق من ترتيب الرتب."),
    "strip_roles": ("سلب الرتب (Strip Roles)", "التحذيرات والإدارة", "manage_roles", ["سلب_الرتب"], "!strip_roles @عضو", "!strip_roles @أحمد", "سحب جميع الرتب القابلة للإزالة من عضو دفعة واحدة."),
    "role_color": ("لون الرتبة (Role Color)", "التحذيرات والإدارة", "manage_roles", ["لون_الرتبة"], "!role_color [الرتبة] [اللون]", "!role_color VIP #FF0055", "تغيير لون رتبة باستخدام كود Hex."),
    "dossier": ("ملف العضو (Dossier)", "التحذيرات والإدارة", "kick_members", ["mod_log", "سجل_العضو"], "!dossier @عضو", "!dossier @أحمد", "تجميع التحذيرات والملاحظات والرتب في ملف إداري واحد."),
    "event_points": ("نقاط الفعاليات (Event Points)", "التحذيرات والإدارة", "manage_events", ["نقاط_الفعاليات"], "!event_points [العضو] [النقاط]", "!event_points @أحمد 10", "عرض أو تعديل نقاط عضو في فعاليات السيرفر."),
    "reset_points": ("تصفير النقاط (Reset Points)", "التحذيرات والإدارة", "administrator", ["ريست_النقاط"], "!reset_points", "!reset_points", "تصفير نقاط الفعاليات لجميع أعضاء السيرفر."),
    "roleall": ("رتبة للكل (Role All)", "أوامر متقدمة", "manage_roles", ["رتبة_للجميع"], "!roleall [الرتبة]", "!roleall VIP", "منح رتبة لجميع الأعضاء القابلين للإدارة مع حماية معدل الطلبات."),
    "removeroleall": ("سحب رتبة الكل (Remove Role All)", "أوامر متقدمة", "manage_roles", ["سحب_رتبة_الكل"], "!removeroleall [الرتبة]", "!removeroleall VIP", "سحب رتبة من جميع الأعضاء القابلين للإدارة."),
    "temprole": ("رتبة مؤقتة (Temp Role)", "أوامر متقدمة", "manage_roles", ["رتبة_مؤقتة"], "!temprole @عضو [الرتبة] [المدة]", "!temprole @أحمد VIP 7d", "منح رتبة مؤقتة وسحبها تلقائياً عند انتهاء المدة."),
    "role_icon": ("أيقونة الرتبة (Role Icon)", "أوامر متقدمة", "manage_roles", ["أيقونة_رتبة"], "!role_icon [الرتبة] [الإيموجي]", "!role_icon VIP ⭐", "تغيير أيقونة رتبة بإيموجي أو صورة مرفقة."),
    "sync_perms": ("مزامنة الصلاحيات (Sync Permissions)", "أوامر متقدمة", "manage_channels", ["مزامنة_الصلاحيات"], "!sync_perms", "!sync_perms", "مزامنة صلاحيات القناة الحالية مع فئتها."),
    "role_members": ("أعضاء الرتبة (Role Members)", "أوامر متقدمة", "manage_roles", ["أعضاء_الرتبة"], "!role_members [الرتبة]", "!role_members VIP", "عرض عدد وأسماء أعضاء رتبة محددة."),
    "no_role": ("بدون رتبة (No Role)", "أوامر متقدمة", "kick_members", ["بدون_رتب"], "!no_role", "!no_role", "عرض الأعضاء الذين لا يحملون أي رتبة إضافية."),
    "bot_list": ("قائمة البوتات (Bot List)", "أوامر متقدمة", "kick_members", ["قائمة_البوتات"], "!bot_list", "!bot_list", "عرض بوتات السيرفر ومعرفاتها."),
    "member_stats": ("إحصائيات الأعضاء (Member Stats)", "أوامر متقدمة", "kick_members", ["إحصائيات_الأعضاء"], "!member_stats", "!member_stats", "عرض إحصائيات البشر والبوتات والمتصلين وحاملي الرتب."),
    "reset_nicks": ("مسح الأسماء (Reset Nicks)", "أوامر متقدمة", "manage_nicknames", ["مسح_الأسماء"], "!reset_nicks", "!reset_nicks", "إعادة الأسماء المستعارة إلى أسماء الأعضاء الأصلية."),
    # أوامر الأعضاء — معلومات
    "help": ("المساعدة (Help)", "أوامر الأعضاء — معلومات", "send_messages", ["مساعدة", "اوامر"], "!help [القسم]", "!help moderation", "عرض دليل أوامر PRIME وأقسامها وطريقة استخدامها."),
    "status": ("الحالة (Status)", "أوامر الأعضاء — معلومات", "send_messages", ["حالة"], "!status", "!status", "عرض صحة البوت والاتصال والخدمات والسيرفرات المتصلة."),
    "ping": ("اختبار الاتصال (Ping)", "أوامر الأعضاء — معلومات", "send_messages", ["بنق"], "!ping", "!ping", "قياس زمن استجابة Discord وزمن معالجة الطلب."),
    "serverinfo": ("معلومات السيرفر (Server Info)", "أوامر الأعضاء — معلومات", "send_messages", ["سيرفر"], "!serverinfo", "!serverinfo", "عرض بيانات السيرفر وعدد الأعضاء والقنوات والرتب."),
    "userinfo": ("معلومات العضو (User Info)", "أوامر الأعضاء — معلومات", "send_messages", ["عضو"], "!userinfo @عضو", "!userinfo @أحمد", "عرض معلومات عضو عامة مثل المعرف وتاريخ الانضمام والرتب."),
    "memberinfo": ("ملف العضو (Member Info)", "أوامر الأعضاء — معلومات", "send_messages", ["ملف"], "!memberinfo @عضو", "!memberinfo @أحمد", "عرض بطاقة معلومات تفصيلية لعضو في السيرفر."),
    "avatar": ("الصورة الشخصية (Avatar)", "أوامر الأعضاء — معلومات", "send_messages", ["صورة"], "!avatar [العضو]", "!avatar @أحمد", "عرض الصورة الشخصية لعضو بالحجم الكامل."),
    "roleinfo": ("معلومات الرتبة (Role Info)", "أوامر الأعضاء — معلومات", "send_messages", ["معلومات_رتبة"], "!roleinfo [الرتبة]", "!roleinfo VIP", "عرض معلومات الرتبة وعدد أعضائها وترتيبها."),
    "emojiinfo": ("معلومات الإيموجي (Emoji Info)", "أوامر الأعضاء — معلومات", "send_messages", ["معلومات_ايموجي"], "!emojiinfo [الإيموجي]", "!emojiinfo :prime:", "عرض معرف الإيموجي ونوعه ورابطه."),
    "channelinfo": ("معلومات القناة (Channel Info)", "أوامر الأعضاء — معلومات", "send_messages", ["معلومات_القناة"], "!channelinfo [القناة]", "!channelinfo #الدردشة", "عرض معلومات القناة الحالية أو قناة يحددها العضو."),
    "botinfo": ("معلومات البوت (Bot Info)", "أوامر الأعضاء — معلومات", "send_messages", ["معلومات_البوت"], "!botinfo", "!botinfo", "عرض إصدار البوت ووقت تشغيله وعدد أوامره."),
    "invite": ("دعوة البوت (Invite)", "أوامر الأعضاء — معلومات", "send_messages", ["دعوة"], "!invite", "!invite", "إنشاء رابط دعوة البوت بالصلاحيات المعلنة."),
    "rules": ("القوانين (Rules)", "أوامر الأعضاء — معلومات", "send_messages", ["قوانين"], "!rules", "!rules", "عرض قوانين السيرفر المنشورة للأعضاء."),
    # أوامر الأعضاء — أدوات
    "profile": ("الملف الشخصي (Profile)", "أوامر الأعضاء — أدوات", "send_messages", ["بروفايل", "ملفي"], "!profile [العضو]", "!profile @أحمد", "عرض ملف العضو ومستواه ومحفظته وإحصائيات نشاطه."),
    "daily": ("المكافأة اليومية (Daily)", "أوامر الأعضاء — أدوات", "send_messages", ["يومي"], "!daily", "!daily", "منح المكافأة اليومية للمستخدم مرة كل فترة استحقاق."),
    "راتب": ("الراتب (Salary)", "أوامر الأعضاء — أدوات", "send_messages", ["راتب", "يومي"], "!راتب", "!راتب", "اختصار عربي لاستلام المكافأة اليومية."),
    "work": ("العمل (Work)", "أوامر الأعضاء — أدوات", "send_messages", ["عمل"], "!work", "!work", "تنفيذ نشاط العمل ومنح عائد اقتصادي عشوائي آمن."),
    "pay": ("الدفع (Pay)", "أوامر الأعضاء — أدوات", "send_messages", ["تحويل"], "!pay @عضو [المبلغ]", "!pay @أحمد 500", "تحويل مبلغ من محفظة المستخدم إلى عضو آخر."),
    "deposit": ("الإيداع (Deposit)", "أوامر الأعضاء — أدوات", "send_messages", ["إيداع"], "!deposit [المبلغ]", "!deposit 500", "إيداع المال من المحفظة في الحساب البنكي."),
    "withdraw": ("السحب (Withdraw)", "أوامر الأعضاء — أدوات", "send_messages", ["سحب"], "!withdraw [المبلغ]", "!withdraw 250", "سحب المال من الحساب البنكي إلى المحفظة."),
    "rob": ("المخاطرة (Rob)", "أوامر الأعضاء — أدوات", "send_messages", ["سرقة"], "!rob @عضو", "!rob @أحمد", "محاولة سرقة اقتصادية بمخاطرة واحتمال عقوبة."),
    "leaderboard": ("المتصدرون (Leaderboard)", "أوامر الأعضاء — أدوات", "send_messages", ["توب", "متصدرين"], "!leaderboard", "!leaderboard", "عرض ترتيب أعضاء السيرفر حسب الثروة."),
    "giveaway": ("السحب (Giveaway)", "أوامر الأعضاء — أدوات", "manage_events", ["سحب", "قرعة"], "!giveaway [المدة] [الجائزة]", "!giveaway 60m رتبة VIP", "إطلاق سحب مؤقت واختيار فائز من المشاركين."),
    "remind": ("تذكير (Remind)", "أوامر الأعضاء — أدوات", "send_messages", ["ذكرني"], "!remind [المدة] [النص]", "!remind 30m راجع البطولة", "حفظ تذكير شخصي وإرساله عند حلول موعده."),
    "reminders": ("تذكيراتي (Reminders)", "أوامر الأعضاء — أدوات", "send_messages", ["تذكيراتي"], "!reminders", "!reminders", "عرض التذكيرات المحفوظة للمستخدم في السيرفر."),
    "reminder_cancel": ("إلغاء تذكير (Reminder Cancel)", "أوامر الأعضاء — أدوات", "send_messages", ["إلغاء_تذكير"], "!reminder_cancel [المعرف]", "!reminder_cancel 12", "إلغاء تذكير محفوظ قبل موعده."),
    "ask": ("اسأل PRIME (Ask)", "أوامر الأعضاء — أدوات", "send_messages", ["سؤال"], "!ask [السؤال]", "!ask كيف أرفع مستوى الأمان؟", "طرح سؤال أو طلب مساعدة ذكية من البوت."),
    "ask_ai": ("الذكاء الاصطناعي (Ask AI)", "أوامر الأعضاء — أدوات", "send_messages", ["ai", "ذكاء"], "!ask_ai [السؤال]", "!ask_ai اشرح لي هذه القاعدة", "إرسال سؤال إلى مساعد الذكاء الاصطناعي للحصول على إجابة تحليلية."),
    "imagine": ("توليد صورة (Imagine)", "أوامر الأعضاء — أدوات", "send_messages", ["صورة_ai"], "!imagine [الوصف]", "!imagine مدينة مستقبلية ليلية", "توليد صورة فنية من وصف نصي."),
    "summarize": ("تلخيص (Summarize)", "أوامر الأعضاء — أدوات", "read_message_history", ["تلخيص"], "!summarize [العدد]", "!summarize 30", "تحليل آخر رسائل القناة وتلخيصها في نقاط."),
    # إدارة القنوات
    "setup_tickets": ("تهيئة التذاكر (Setup Tickets)", "إدارة القنوات", "manage_channels", ["تذاكر"], "!setup_tickets", "!setup_tickets", "تثبيت لوحة تذاكر الدعم الفني وإعداد قنواتها."),
    "ticket_reply": ("رد التذكرة (Ticket Reply)", "إدارة القنوات", "manage_channels", ["رد_تذكرة"], "!ticket_reply [النص]", "!ticket_reply أهلاً بك، نراجع طلبك الآن", "إرسال رد سريع ومنسق داخل التذكرة الحالية."),
    "ticket_close": ("إغلاق التذكرة (Ticket Close)", "إدارة القنوات", "manage_channels", ["إغلاق_تذكرة"], "!ticket_close [السبب]", "!ticket_close تم الحل", "إغلاق التذكرة وأرشفة محتواها وفق سياسة الدعم."),
    "ticket_transcript": ("سجل التذكرة (Ticket Transcript)", "إدارة القنوات", "manage_channels", ["نسخة_تذكرة"], "!ticket_transcript", "!ticket_transcript", "تصدير سجل التذكرة إلى أرشيف قابل للمراجعة."),
    "setup_roles": ("تهيئة الرتب (Setup Roles)", "إدارة القنوات", "manage_roles", ["رتب"], "!setup_roles", "!setup_roles", "تثبيت لوحة الرتب التفاعلية للأعضاء."),
    "setup_rules": ("تهيئة القوانين (Setup Rules)", "إدارة القنوات", "manage_channels", ["قوانين_تثبيت"], "!setup_rules", "!setup_rules", "تثبيت بوابة الموافقة على القوانين في القناة المحددة."),
    "setup_counters": ("عدادات السيرفر (Setup Counters)", "إدارة القنوات", "manage_channels", ["عدادات"], "!setup_counters", "!setup_counters", "تثبيت قنوات صوتية تعرض إحصائيات السيرفر الحية."),
    "setup_voice": ("الرومات المؤقتة (Setup Voice)", "إدارة القنوات", "administrator", ["رومات"], "!setup_voice", "!setup_voice", "تهيئة نظام رومات صوتية مؤقتة ذاتية الإدارة."),
    # أوامر متقدمة
    "transcript": ("أرشيف الرسائل (Transcript)", "أوامر متقدمة", "manage_messages", ["أرشفة"], "!transcript [العدد]", "!transcript 100", "تصدير رسائل القناة إلى ملف أرشيف نصي."),
    "backup_structure": ("نسخة الهيكل (Backup Structure)", "أوامر متقدمة", "administrator", ["نسخة_احتياطية"], "!backup_structure", "!backup_structure", "أخذ نسخة احتياطية من هيكل السيرفر ورتبه وإعداداته."),
    "backup_restore": ("استعادة الهيكل (Backup Restore)", "أوامر متقدمة", "administrator", ["استعادة"], "!backup_restore [المعرف]", "!backup_restore latest", "استعادة هيكل محفوظ بعد التأكيد والتحقق من صلاحيات المدير."),
    "imagine": ("توليد صورة (Imagine)", "أوامر متقدمة", "send_messages", ["صورة_ai"], "!imagine [الوصف]", "!imagine شعار أزرق مستقبلي", "توليد صورة فنية رقمية من وصف المستخدم."),
    "scrim_open": ("فتح السكريم (Scrim Open)", "أوامر متقدمة", "manage_events", ["سكريم"], "!scrim_open [العنوان] [اللعبة]", "!scrim_open بطولة Valorant", "فتح لوحة تسجيل سكريم للفرق وإدارة المقاعد."),
    "scrim_close": ("إغلاق السكريم (Scrim Close)", "أوامر متقدمة", "manage_events", ["إغلاق_سكريم"], "!scrim_close", "!scrim_close", "إغلاق تسجيل السكريم الحالي ومنع المشاركات الجديدة."),
    "scrim_split": ("تقسيم السكريم (Scrim Split)", "أوامر متقدمة", "move_members", ["تقسيم"], "!scrim_split [عدد الفرق]", "!scrim_split 2", "توزيع لاعبي الروم الصوتي بالتساوي بين الفرق."),
    "scrim_teams": ("فرق السكريم (Scrim Teams)", "أوامر متقدمة", "move_members", ["فرق"], "!scrim_teams [النمط]", "!scrim_teams squad", "تقسيم لاعبي الروم إلى فرق بالنمط المحدد."),
    "map_randomizer": ("اختيار الخريطة (Map Randomizer)", "أوامر متقدمة", "send_messages", ["خريطة"], "!map_randomizer [القائمة]", "!map_randomizer ranked", "اختيار خريطة عشوائية لمباراة تكتيكية."),
    "tournament_open": ("فتح البطولة (Tournament Open)", "أوامر متقدمة", "manage_events", ["بطولة"], "!tournament_open [العنوان]", "!tournament_open PRIME CUP", "فتح التسجيل لبطولة رسمية وتوليد جدولها."),
    "tournament_close": ("إغلاق البطولة (Tournament Close)", "أوامر متقدمة", "manage_events", ["إغلاق_بطولة"], "!tournament_close", "!tournament_close", "إغلاق التسجيل في البطولة الحالية."),
    "match_record": ("تسجيل المباراة (Match Record)", "أوامر متقدمة", "manage_events", ["نتيجة"], "!match_record [الفائز] [النتيجة]", "!match_record TeamA 2-1", "توثيق نتيجة مباراة وتوزيع نقاط الفوز."),
    "standings": ("ترتيب البطولة (Standings)", "أوامر متقدمة", "send_messages", ["ترتيب"], "!standings", "!standings", "عرض ترتيب فرق البطولات المحفوظ."),
    "set_leaderboard_channel": ("قناة المتصدرين (Leaderboard Channel)", "أوامر متقدمة", "manage_channels", ["قناة_المتصدرين"], "!set_leaderboard_channel [القناة]", "!set_leaderboard_channel #المتصدرين", "تثبيت لوحة المتصدرين الحية في قناة محددة."),
    "give_points": ("إضافة نقاط (Give Points)", "أوامر متقدمة", "administrator", ["نقاط"], "!give_points @عضو [العدد]", "!give_points @أحمد 100", "إضافة نقاط إلى محفظة عضو بصلاحية الإدارة."),
    "take_points": ("خصم نقاط (Take Points)", "أوامر متقدمة", "administrator", ["خصم_نقاط"], "!take_points @عضو [العدد]", "!take_points @أحمد 50", "خصم نقاط من محفظة عضو مع تسجيل العملية."),
    "اعطاء_نقاط": ("إضافة نقاط عربية (Give Points)", "أوامر متقدمة", "administrator", ["give-points"], "!اعطاء_نقاط @عضو [العدد]", "!اعطاء_نقاط @أحمد 100", "اختصار عربي لإضافة نقاط إلى محفظة عضو."),
    "سحب_نقاط": ("سحب نقاط عربية (Take Points)", "أوامر متقدمة", "administrator", ["take-points"], "!سحب_نقاط @عضو [العدد]", "!سحب_نقاط @أحمد 50", "اختصار عربي لخصم نقاط من محفظة عضو."),
    "radio": ("الراديو (Radio)", "أوامر متقدمة", "connect", ["إذاعة"], "!radio", "!radio", "تشغيل إذاعة القرآن المباشرة في الروم الصوتي."),
    # السجل والملاحظات
    "snipe": ("آخر رسالة محذوفة (Snipe)", "السجل والملاحظات", "manage_messages", ["سناب"], "!snipe", "!snipe", "عرض آخر رسالة حُذفت من القناة إن كانت محفوظة."),
    "editsnipe": ("آخر تعديل (Edit Snipe)", "السجل والملاحظات", "manage_messages", ["تعديل"], "!editsnipe", "!editsnipe", "عرض آخر رسالة تم تعديلها مع النص السابق والجديد."),
    "history": ("سجل العضو (History)", "السجل والملاحظات", "view_audit_log", ["تاريخ"], "!history @عضو", "!history @أحمد", "عرض التاريخ الإداري والإنذارات المسجلة لعضو."),
    "suggest": ("اقتراح (Suggest)", "السجل والملاحظات", "send_messages", ["اقتراح"], "!suggest [النص]", "!suggest إضافة قناة للألعاب", "إرسال اقتراح وطرحه للتصويت والإدارة."),
    "transcript_log": ("سجل المحادثة (Transcript Log)", "السجل والملاحظات", "read_message_history", ["سجل_المحادثة"], "!transcript_log [العدد]", "!transcript_log 50", "عرض سجل مختصر لرسائل القناة للمراجعة."),
    "reminders_log": ("سجل التذكيرات (Reminders Log)", "السجل والملاحظات", "manage_messages", ["سجل_التذكيرات"], "!reminders_log", "!reminders_log", "عرض عمليات إنشاء وإلغاء التذكيرات في السيرفر."),
    # إحصائيات وتراجع
    "leaderboard": ("المتصدرون (Leaderboard)", "إحصائيات وتراجع", "send_messages", ["ترتيب_الأعضاء"], "!leaderboard", "!leaderboard", "عرض ترتيب أعضاء السيرفر حسب الثروة."),
    "stats": ("إحصائيات (Stats)", "إحصائيات وتراجع", "send_messages", ["إحصائيات"], "!stats [الفترة]", "!stats week", "عرض إحصائيات النشاط والأعضاء خلال فترة محددة."),
    "analytics": ("تحليل النشاط (Analytics)", "إحصائيات وتراجع", "view_audit_log", ["تحليل"], "!analytics [الفترة]", "!analytics month", "تحليل اتجاهات النشاط والتفاعل في السيرفر."),
    "rollback": ("تراجع آمن (Rollback)", "إحصائيات وتراجع", "administrator", ["تراجع"], "!rollback [المعرف]", "!rollback last", "التراجع عن إعداد مدعوم بعد تأكيد المدير وحفظ سجل العملية."),
    "giveaway_end": ("إنهاء السحب (Giveaway End)", "إحصائيات وتراجع", "manage_events", ["إنهاء_السحب"], "!giveaway_end [المعرف]", "!giveaway_end 12", "إنهاء سحب قائم واختيار الفائزين وفق الحالة الحالية."),
    "giveaway_reroll": ("إعادة السحب (Giveaway Reroll)", "إحصائيات وتراجع", "manage_events", ["إعادة_السحب"], "!giveaway_reroll [المعرف]", "!giveaway_reroll 12", "اختيار فائز بديل لسحب منتهٍ عند الحاجة."),
}

# Step 5 keeps its syntax and policy metadata in the same registry as the
# earlier command families.  The callbacks remain in tools_channels.py, while
# this data stays import-safe for the dashboard and help surfaces.
_STEP5_COMMANDS = {
    "avatar": ("أفتار", "أوامر الأعضاء — معلومات", "send_messages"),
    "banner": ("بانر", "أوامر الأعضاء — معلومات", "send_messages"),
    "userinfo": ("معلومات عضو", "أوامر الأعضاء — معلومات", "send_messages"),
    "serverinfo": ("معلومات السيرفر", "أوامر الأعضاء — معلومات", "send_messages"),
    "roleinfo": ("معلومات رتبة", "أوامر الأعضاء — معلومات", "send_messages"),
    "ping": ("بينغ", "أوامر الأعضاء — معلومات", "send_messages"),
    "serverheader": ("هيدر السيرفر", "أوامر الأعضاء — معلومات", "send_messages"),
    "roles": ("الرتب", "أوامر الأعضاء — معلومات", "send_messages"),
    "emojis": ("الإيموجي", "أوامر الأعضاء — معلومات", "send_messages"),
    "joinposition": ("ترتيب الانضمام", "أوامر الأعضاء — معلومات", "send_messages"),
    "mutual": ("السيرفرات المشتركة", "أوامر الأعضاء — معلومات", "send_messages"),
    "whois": ("فحص العضو", "أوامر الأعضاء — معلومات", "send_messages"),
    "channelinfo": ("معلومات قناة", "أوامر الأعضاء — معلومات", "send_messages"),
    "rolemembers": ("أعضاء الرتبة", "أوامر الأعضاء — معلومات", "send_messages"),
    "snipe": ("آخر رسالة محذوفة", "أوامر الأعضاء — أدوات", "manage_messages"),
    "editsnipe": ("آخر تعديل", "أوامر الأعضاء — أدوات", "manage_messages"),
    "firstmsg": ("أول رسالة", "أوامر الأعضاء — أدوات", "read_message_history"),
    "steal_emoji": ("سرقة إيموجي", "أوامر الأعضاء — أدوات", "manage_emojis"),
    "enlarge_emoji": ("تكبير إيموجي", "أوامر الأعضاء — أدوات", "send_messages"),
    "remind": ("تذكير", "أوامر الأعضاء — أدوات", "send_messages"),
    "countdown": ("عداد", "أوامر الأعضاء — أدوات", "send_messages"),
    "color": ("معاينة لون", "أوامر الأعضاء — أدوات", "send_messages"),
    "encode": ("تشفير Base64", "أوامر الأعضاء — أدوات", "send_messages"),
    "decode": ("فك Base64", "أوامر الأعضاء — أدوات", "send_messages"),
    "quote": ("اقتباس", "أوامر الأعضاء — أدوات", "read_message_history"),
    "timestamp": ("وقت Discord", "أوامر الأعضاء — أدوات", "send_messages"),
    "steal_sticker": ("سرقة ستيكر", "أوامر الأعضاء — أدوات", "manage_emojis"),
    "create_channel": ("إنشاء روم", "إدارة القنوات", "manage_channels"),
    "delete_channel": ("حذف روم", "إدارة القنوات", "manage_channels"),
    "rename_channel": ("تغيير اسم روم", "إدارة القنوات", "manage_channels"),
    "move_channel": ("نقل روم", "إدارة القنوات", "manage_channels"),
    "set_topic": ("توبيك الروم", "إدارة القنوات", "manage_channels"),
    "clone_channel": ("نسخ روم", "إدارة القنوات", "manage_channels"),
    "create_voice": ("إنشاء روم صوتي", "إدارة القنوات", "manage_channels"),
    "delete_voice": ("حذف روم صوتي", "إدارة القنوات", "manage_channels"),
    "rename_voice": ("تغيير اسم صوتي", "إدارة القنوات", "manage_channels"),
    "move_voice": ("نقل روم صوتي", "إدارة القنوات", "manage_channels"),
    "mod_stats": ("إحصائيات المشرف", "إحصائيات وتراجع", "view_audit_log"),
    "undo_action": ("تراجع عن إجراء", "إحصائيات وتراجع", "moderate_members"),
    "security_report": ("تقرير المخاطر", "إحصائيات وتراجع", "administrator"),
}
_COMMAND_SPECS.update(
    {
        key: (
            display,
            category,
            permission,
            [],
            f"!{key} [الخيارات]",
            f"!{key}",
            f"{display} ضمن حزمة Step 5 مع احترام سياسات الأوامر وسجل التدقيق.",
        )
        for key, (display, category, permission) in _STEP5_COMMANDS.items()
        if key not in _COMMAND_SPECS
    }
)

# Keep the public mapping stable and include the key in every metadata object.
MASTER_COMMANDS_REGISTRY = {
    key: _command(key, *spec)
    for key, spec in _COMMAND_SPECS.items()
}


def grouped_command_registry() -> list[dict]:
    """Return a JSON-ready, category-ordered copy for the dashboard API."""
    grouped = {category: [] for category in COMMAND_CATEGORIES}
    for metadata in MASTER_COMMANDS_REGISTRY.values():
        grouped.setdefault(metadata["category"], []).append(deepcopy(metadata))
    return [
        {
            "category": category,
            "commands": sorted(
                grouped[category],
                key=lambda item: (item["display_name"], item["key"]),
            ),
        }
        for category in COMMAND_CATEGORIES
        if grouped[category]
    ]


def command_metadata(command_name: str) -> dict | None:
    """Return a defensive copy so request handlers cannot mutate the registry."""
    item = MASTER_COMMANDS_REGISTRY.get(str(command_name).strip().lower())
    return deepcopy(item) if item else None
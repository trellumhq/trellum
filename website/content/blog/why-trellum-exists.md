# Why {{BRAND}} exists

I didn't set out to build a reporting framework. I wanted answers from my data, faster, with AI. {{BRAND}} is what was left standing after everything that didn't work.

## Dashboards took too long, even before AI

The old way was manual. Building a dashboard meant hours of clicking, arranging and polishing. I spent a lot of time learning visual dashboard tools and still had more to learn. And when agents arrived they couldn't help me there: a drag-and-drop tool has no code for an agent to write.

## So the agents got a folder

My AI workflow became a folder. Queries, notes, schema descriptions, a few Python scripts. Context I could point an agent at so it would stop guessing what "revenue" meant. Colleagues used it too, and since asking was suddenly cheap, the ad-hoc analyses multiplied. Another question, another agent-generated dashboard, another HTML file.

## The one-off HTML trend

Honestly, those dashboards were garbage in nice clothing. Giant HTML files with the data baked in, numbers that were sometimes hallucinated, and no two regenerations that ever matched.

In practice that hurt, because I needed numbers quickly and they had to look good enough to go straight into presentations. So I was stuck in a loop: ask the AI for a dashboard, get something not quite presentable, rework it by hand, run out of time, ask again. Publishing one decent set of charts could eat my whole day.

The strange part: people loved them anyway. And then someone asked the obvious question:

> "This is great. Can we get it every day?"

I did it, and it worked. But only because I wrote custom code every time to pin the layout and the numbers down, which cost me a lot of time. And the smallest change still made the agent query all the data again and rebuild the whole report. Minutes per iteration, at best.

## Make the agent write code instead

The fix was not a better prompt. It was moving the AI to the other side of the line:

<figure>
<div class="mfig">
<div class="mrow">
<div class="mhead"><span class="flab">before</span><span class="mflow">you prompt → the AI improvises the whole report, every day</span></div>
<div class="wkcells">
<div class="wc"><svg viewBox="0 0 80 54"><rect x="3" y="3" width="28" height="3" rx="1.5" fill="var(--text3)" opacity=".5"/><rect x="3" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="5" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="5" y="14.5" width="12" height="3" rx="1" fill="var(--text2)"/><rect x="29" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="31" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="31" y="14.5" width="14" height="3" rx="1" fill="var(--text2)"/><rect x="55" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="57" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="57" y="14.5" width="10" height="3" rx="1" fill="var(--text2)"/><line x1="3" y1="38" x2="77" y2="38" stroke="var(--border)" stroke-width="1" stroke-dasharray="2 2"/><line x1="3" y1="50" x2="77" y2="50" stroke="var(--border)" stroke-width="1"/><rect x="4" y="41" width="9" height="9" fill="var(--ch1)"/><rect x="17" y="37" width="9" height="13" fill="var(--ch1)"/><rect x="30" y="44" width="9" height="6" fill="var(--ch1)"/><rect x="43" y="34" width="9" height="16" fill="var(--ch1)"/><rect x="56" y="39" width="9" height="11" fill="var(--ch1)"/><rect x="68" y="36" width="9" height="14" fill="var(--ch1)"/></svg><span>Mon</span></div>
<div class="wc"><svg viewBox="0 0 80 54"><rect x="3" y="3" width="22" height="3" rx="1.5" fill="var(--text3)" opacity=".5"/><rect x="3" y="9" width="20" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="5" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="5" y="14.5" width="10" height="3" rx="1" fill="var(--text2)"/><rect x="27" y="9" width="20" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="29" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="29" y="14.5" width="13" height="3" rx="1" fill="var(--text2)"/><rect x="51" y="9" width="20" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="53" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="53" y="14.5" width="11" height="3" rx="1" fill="var(--text2)"/><line x1="3" y1="38" x2="77" y2="38" stroke="var(--border)" stroke-width="1" stroke-dasharray="2 2"/><line x1="3" y1="50" x2="77" y2="50" stroke="var(--border)" stroke-width="1"/><rect x="4" y="40" width="9" height="10" fill="var(--ch3)"/><rect x="17" y="39" width="9" height="11" fill="var(--ch3)"/><rect x="30" y="41" width="9" height="9" fill="var(--ch3)"/><rect x="43" y="36" width="9" height="14" fill="var(--ch3)"/><rect x="56" y="38" width="9" height="12" fill="var(--ch3)"/><rect x="68" y="34" width="9" height="16" fill="var(--ch3)"/></svg><span>Tue</span></div>
<div class="wc"><svg viewBox="0 0 80 54"><rect x="3" y="3" width="34" height="3" rx="1.5" fill="var(--text3)" opacity=".5"/><rect x="3" y="9" width="26" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="5" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="5" y="14.5" width="14" height="3" rx="1" fill="var(--text2)"/><rect x="33" y="9" width="26" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="35" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="35" y="14.5" width="12" height="3" rx="1" fill="var(--text2)"/><line x1="3" y1="38" x2="77" y2="38" stroke="var(--border)" stroke-width="1" stroke-dasharray="2 2"/><line x1="3" y1="50" x2="77" y2="50" stroke="var(--border)" stroke-width="1"/><rect x="4" y="38" width="9" height="12" fill="var(--ch2)"/><rect x="17" y="37" width="9" height="13" fill="var(--ch2)"/><rect x="30" y="43" width="9" height="7" fill="var(--ch2)"/><rect x="43" y="34" width="9" height="16" fill="var(--ch2)"/><rect x="56" y="39" width="9" height="11" fill="var(--ch2)"/><rect x="68" y="36" width="9" height="14" fill="var(--ch2)"/><polyline points="8,42 21,45 34,36 47,44 60,39 72,41" fill="none" stroke="var(--ch3)" stroke-width="1.5"/></svg><span>Wed</span></div>
<div class="wc"><svg viewBox="0 0 80 54"><rect x="5" y="3" width="28" height="3" rx="1.5" fill="var(--text3)" opacity=".5"/><rect x="5" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="7" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="7" y="14.5" width="11" height="3" rx="1" fill="var(--text2)"/><rect x="31" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="33" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="33" y="14.5" width="13" height="3" rx="1" fill="var(--text2)"/><rect x="57" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="59" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="59" y="14.5" width="12" height="3" rx="1" fill="var(--text2)"/><line x1="3" y1="38" x2="77" y2="38" stroke="var(--border)" stroke-width="1" stroke-dasharray="2 2"/><line x1="3" y1="50" x2="77" y2="50" stroke="var(--border)" stroke-width="1"/><rect x="4" y="39" width="9" height="11" fill="var(--scope-studio)"/><rect x="17" y="34" width="9" height="16" fill="var(--scope-studio)"/><rect x="30" y="42" width="9" height="8" fill="var(--scope-studio)"/><rect x="43" y="33" width="9" height="17" fill="var(--scope-studio)"/><rect x="56" y="38" width="9" height="12" fill="var(--scope-studio)"/><rect x="68" y="32" width="9" height="18" fill="var(--scope-studio)"/><rect x="3" y="51" width="18" height="2" rx="1" fill="var(--ch3)" opacity=".6"/></svg><span>Thu</span></div>
</div>
</div>
<div class="mrow tr">
<div class="mhead"><span class="flab">after</span><span class="mflow">the agent writes code once → scheduled builds, every morning</span></div>
<div class="wkcells">
<div class="wc"><svg viewBox="0 0 80 54"><rect x="3" y="3" width="28" height="3" rx="1.5" fill="var(--text3)" opacity=".5"/><rect x="3" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="5" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="5" y="14.5" width="12" height="3" rx="1" fill="var(--text2)"/><rect x="29" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="31" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="31" y="14.5" width="14" height="3" rx="1" fill="var(--text2)"/><rect x="55" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="57" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="57" y="14.5" width="10" height="3" rx="1" fill="var(--text2)"/><line x1="3" y1="38" x2="77" y2="38" stroke="var(--border)" stroke-width="1" stroke-dasharray="2 2"/><line x1="3" y1="50" x2="77" y2="50" stroke="var(--border)" stroke-width="1"/><rect x="4" y="41" width="9" height="9" fill="var(--ch1)"/><rect x="17" y="37" width="9" height="13" fill="var(--ch1)"/><rect x="30" y="44" width="9" height="6" fill="var(--ch1)"/><rect x="43" y="34" width="9" height="16" fill="var(--ch1)"/><rect x="56" y="39" width="9" height="11" fill="var(--ch1)"/><rect x="68" y="36" width="9" height="14" fill="var(--ch1)"/></svg><span>Mon</span></div>
<div class="wc"><svg viewBox="0 0 80 54"><rect x="3" y="3" width="28" height="3" rx="1.5" fill="var(--text3)" opacity=".5"/><rect x="3" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="5" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="5" y="14.5" width="12" height="3" rx="1" fill="var(--text2)"/><rect x="29" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="31" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="31" y="14.5" width="14" height="3" rx="1" fill="var(--text2)"/><rect x="55" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="57" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="57" y="14.5" width="10" height="3" rx="1" fill="var(--text2)"/><line x1="3" y1="38" x2="77" y2="38" stroke="var(--border)" stroke-width="1" stroke-dasharray="2 2"/><line x1="3" y1="50" x2="77" y2="50" stroke="var(--border)" stroke-width="1"/><rect x="4" y="40" width="9" height="10" fill="var(--ch1)"/><rect x="17" y="38" width="9" height="12" fill="var(--ch1)"/><rect x="30" y="42" width="9" height="8" fill="var(--ch1)"/><rect x="43" y="35" width="9" height="15" fill="var(--ch1)"/><rect x="56" y="37" width="9" height="13" fill="var(--ch1)"/><rect x="68" y="33" width="9" height="17" fill="var(--ch1)"/></svg><span>Tue</span></div>
<div class="wc"><svg viewBox="0 0 80 54"><rect x="3" y="3" width="28" height="3" rx="1.5" fill="var(--text3)" opacity=".5"/><rect x="3" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="5" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="5" y="14.5" width="12" height="3" rx="1" fill="var(--text2)"/><rect x="29" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="31" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="31" y="14.5" width="14" height="3" rx="1" fill="var(--text2)"/><rect x="55" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="57" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="57" y="14.5" width="10" height="3" rx="1" fill="var(--text2)"/><line x1="3" y1="38" x2="77" y2="38" stroke="var(--border)" stroke-width="1" stroke-dasharray="2 2"/><line x1="3" y1="50" x2="77" y2="50" stroke="var(--border)" stroke-width="1"/><rect x="4" y="38" width="9" height="12" fill="var(--ch1)"/><rect x="17" y="36" width="9" height="14" fill="var(--ch1)"/><rect x="30" y="43" width="9" height="7" fill="var(--ch1)"/><rect x="43" y="33" width="9" height="17" fill="var(--ch1)"/><rect x="56" y="38" width="9" height="12" fill="var(--ch1)"/><rect x="68" y="35" width="9" height="15" fill="var(--ch1)"/></svg><span>Wed</span></div>
<div class="wc"><svg viewBox="0 0 80 54"><rect x="3" y="3" width="28" height="3" rx="1.5" fill="var(--text3)" opacity=".5"/><rect x="3" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="5" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="5" y="14.5" width="12" height="3" rx="1" fill="var(--text2)"/><rect x="29" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="31" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="31" y="14.5" width="14" height="3" rx="1" fill="var(--text2)"/><rect x="55" y="9" width="22" height="11" rx="1.5" fill="none" stroke="var(--border)" stroke-width="1"/><rect x="57" y="11.5" width="8" height="1.5" rx=".75" fill="var(--text3)" opacity=".6"/><rect x="57" y="14.5" width="10" height="3" rx="1" fill="var(--text2)"/><line x1="3" y1="38" x2="77" y2="38" stroke="var(--border)" stroke-width="1" stroke-dasharray="2 2"/><line x1="3" y1="50" x2="77" y2="50" stroke="var(--border)" stroke-width="1"/><rect x="4" y="39" width="9" height="11" fill="var(--ch1)"/><rect x="17" y="35" width="9" height="15" fill="var(--ch1)"/><rect x="30" y="41" width="9" height="9" fill="var(--ch1)"/><rect x="43" y="34" width="9" height="16" fill="var(--ch1)"/><rect x="56" y="37" width="9" height="13" fill="var(--ch1)"/><rect x="68" y="31" width="9" height="19" fill="var(--ch1)"/></svg><span>Thu</span></div>
</div>
</div>
</div>
</figure>

The agent still does the creative work. It just commits code instead of re-performing the report. That is {{BRAND}}: an open-source Python framework for BI reports written as code, built deterministically.

Building it was a process. But because it came out of a real necessity, it turned into something my peers and I genuinely love using. The report is just there every morning, and I don't think about it anymore. I think others will love that too.

## Separate the data from the report

The other half of the fix: getting the data and building the report are two separate steps.

<div class="bafig solo">
<span class="bnode"><span class="srcmini"><svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M7.602 12.4c.038-.151.076-.304.076-.456 0-.114-.038-.228-.038-.342-.114-.343-.304-.647-.646-.838l-4.87-2.777c-.685-.38-1.56-.152-1.94.533-.381.685-.153 1.56.532 1.94l2.701 1.56-2.701 1.56c-.685.38-.913 1.256-.533 1.94.38.685 1.256.914 1.94.533l4.832-2.777c.343-.267.571-.533.647-.876zm1.332 2.626c-.266-.038-.57.038-.837.19l-4.832 2.777c-.685.38-.913 1.256-.532 1.94.38.686 1.255.914 1.94.533l2.701-1.56v3.12c0 .8.647 1.408 1.446 1.408.799 0 1.407-.647 1.407-1.408v-5.592c0-.761-.57-1.37-1.293-1.408zm4.946-6.088c.266.038.57-.038.837-.19l4.832-2.777c.685-.38.913-1.256.532-1.94-.38-.686-1.255-.914-1.94-.533l-2.701 1.56V1.975c0-.799-.647-1.408-1.446-1.408-.799 0-1.446.609-1.446 1.408V7.53c0 .76.609 1.37 1.332 1.407zM3.265 5.97l4.832 2.777c.266.152.533.19.837.19.723-.038 1.331-.684 1.331-1.407V1.975c0-.799-.646-1.408-1.407-1.408-.799 0-1.446.647-1.446 1.408v3.12l-2.701-1.56c-.685-.38-1.56-.152-1.94.533-.419.646-.19 1.521.494 1.902zm9.093 6.011a.412.412 0 00-.114-.266l-.57-.571a.346.346 0 00-.267-.114.412.412 0 00-.266.114l-.571.57a.411.411 0 00-.114.267c0 .076.038.19.114.267l.57.57a.345.345 0 00.267.114c.076 0 .19-.038.266-.114l.571-.57a.412.412 0 00.114-.267zm1.598.533L11.94 14.53c-.039.038-.153.114-.229.114h-.608a.411.411 0 01-.267-.114L8.82 12.514a.408.408 0 01-.076-.229v-.608c0-.076.038-.19.114-.267l2.016-2.016a.41.41 0 01.267-.114h.608a.41.41 0 01.267.114l2.016 2.016a.347.347 0 01.114.267v.608c-.076.077-.114.19-.19.229zm5.593 5.44l-4.832-2.777c-.266-.152-.57-.19-.837-.152-.723.038-1.332.684-1.332 1.408v5.554c0 .8.647 1.408 1.408 1.408.799 0 1.446-.647 1.446-1.408v-3.12l2.7 1.56c.686.38 1.561.152 1.941-.533.419-.646.19-1.521-.494-1.94zm2.549-7.533l-2.701 1.56 2.7 1.56c.686.38.914 1.256.533 1.94-.38.685-1.255.913-1.94.533l-4.832-2.778a1.644 1.644 0 01-.647-.798c-.037-.153-.076-.305-.076-.457 0-.114.039-.228.039-.342.114-.343.342-.647.646-.837l4.832-2.778c.685-.38 1.56-.152 1.94.533.457.609.19 1.484-.494 1.864"/></svg><svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M5.676 10.595h2.052v5.244a5.892 5.892 0 0 1-2.052-2.088v-3.156zm18.179 10.836a.504.504 0 0 1 0 .708l-1.716 1.716a.504.504 0 0 1-.708 0l-4.248-4.248a.206.206 0 0 1-.007-.007c-.02-.02-.028-.045-.043-.066a10.736 10.736 0 0 1-6.334 2.065C4.835 21.599 0 16.764 0 10.799S4.835 0 10.8 0s10.799 4.835 10.799 10.8c0 2.369-.772 4.553-2.066 6.333.025.017.052.028.074.05l4.248 4.248zm-5.028-10.632a8.015 8.015 0 1 0-8.028 8.028h.024a8.016 8.016 0 0 0 8.004-8.028zm-4.86 4.98a6.002 6.002 0 0 0 2.04-2.184v-1.764h-2.04v3.948zm-4.5.948c.442.057.887.08 1.332.072.4.025.8.025 1.2 0V7.692H9.468v9.035z"/></svg><svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M12 0C5.363 0 0 5.363 0 12s5.363 12 12 12 12-5.363 12-12S18.637 0 12 0zM9.502 7.03a4.974 4.974 0 0 1 4.97 4.97 4.974 4.974 0 0 1-4.97 4.97A4.974 4.974 0 0 1 4.532 12a4.974 4.974 0 0 1 4.97-4.97zm6.563 3.183h2.351c.98 0 1.787.782 1.787 1.762s-.807 1.789-1.787 1.789h-2.351v-3.551z"/></svg><svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M11.318 12.545H7.91v-1.909h3.41v1.91zM14.728 0v6h6l-6-6zm1.363 10.636h-3.41v1.91h3.41v-1.91zm0 3.273h-3.41v1.91h3.41v-1.91zM20.727 6.5v15.864c0 .904-.732 1.636-1.636 1.636H4.909a1.636 1.636 0 0 1-1.636-1.636V1.636C3.273.732 4.005 0 4.909 0h9.318v6.5h6.5zm-3.273 2.773H6.545v7.909h10.91v-7.91zm-6.136 4.636H7.91v1.91h3.41v-1.91z"/></svg></span>any source, combined</span>
<svg class="farrow" viewBox="0 0 24 10" width="24" height="10" aria-hidden="true"><path d="M0 5 H18 M14 1.5 L19 5 L14 8.5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
<span class="bnode hi">compiled dataset · cached</span>
<svg class="farrow" viewBox="0 0 24 10" width="24" height="10" aria-hidden="true"><path d="M0 5 H18 M14 1.5 L19 5 L14 8.5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
<span class="bnode">report build, in seconds</span>
</div>

{{BRAND}} pulls from pretty much anything (databases, warehouses, files, APIs) and compiles it into one efficient dataset. Reports build from that dataset, so once the data is queried, iterating takes seconds instead of minutes, even on large data. Themes and shared annotations ride on the same build: define once, every report follows.

## What's in the box

Everything I kept rebuilding by hand, built in:

<div class="bslegend">
<span><i style="background:var(--ch1)"></i>display</span>
<span><i style="background:var(--blue)"></i>interaction</span>
<span><i style="background:var(--ch3)"></i>statistics</span>
<span><i style="background:var(--scope-studio)"></i>report-wide</span>
</div>

<div class="boxspec">
<span class="bcell"><i style="background:var(--ch1)"></i>line · area · bar · stacked</span>
<span class="bcell"><i style="background:var(--ch1)"></i>combo · doughnut · scatter</span>
<span class="bcell"><i style="background:var(--ch1)"></i>heatmap · funnel · treemap</span>
<span class="bcell"><i style="background:var(--ch1)"></i>KPI rows</span>
<span class="bcell"><i style="background:var(--ch1)"></i>tables &amp; pivots</span>
<span class="bcell"><i style="background:var(--blue)"></i>filters &amp; cross-filtering</span>
<span class="bcell"><i style="background:var(--blue)"></i>date selectors</span>
<span class="bcell"><i style="background:var(--blue)"></i>tab groups</span>
<span class="bcell"><i style="background:var(--ch3)"></i>A/B compare</span>
<span class="bcell"><i style="background:var(--ch3)"></i>CUPED &amp; bootstrap CIs</span>
<span class="bcell"><i style="background:var(--scope-studio)"></i>AI context widgets</span>
<span class="bcell"><i style="background:var(--scope-studio)"></i>CSV &amp; PDF export</span>
<span class="bcell"><i style="background:var(--scope-studio)"></i>themes &amp; custom themes</span>
<span class="bcell"><i style="background:var(--scope-studio)"></i>shared annotations</span>
</div>

And when none of it fits, your agent can invent any HTML visualization it likes. The framework knows how to build it in as a first-class component: same theme, same filters, same shared data.

## This is for everyone

Data scientists and analysts will probably feel it first. But {{BRAND}} is not a specialist tool. If you have an agent and a question, you can have a report: marketing, ops, finance, founders, anyone. Ask for it once, review it once, and every morning it rebuilds itself. Same numbers for everyone, no fiddling, no redoing, no wondering why today's version looks different.

Making charts used to be the work. Now the work is asking better questions. Go ask one.

## Sixty seconds to a report

```
$ python -m venv .venv && . .venv/bin/activate
$ python -m pip install https://github.com/trellumhq/trellum/releases/download/v0.1.0/trellum-0.1.0-py3-none-any.whl
$ python -m trellum.demo --dest demo-project
$ cd demo-project
$ python -m trellum.run reports/player-overview --no-serve --portable
$ python -m trellum serve --background
```

Or skip even that. My honest recommendation is to not set anything up yourself and let your agent do it:

<div class="agentline"><span class="tag">paste to your agent</span><button class="copybtn" type="button" data-copy="agent-prompt-post">copy</button><code id="agent-prompt-post">{{AGENT_PROMPT}}</code></div>

Open the result in your browser, export the PDF, send it. Explore the [demo gallery]({{DEMO_URL}}) or start from the [repository README]({{FRAMEWORK_REPO}}).

<div class="signoff">
<p>{{BRAND}} is a true passion project. It came out of necessity, it was built with love, and I hope everyone enjoys it as much as I do.</p>
<span class="sig">— Apollo</span>
</div>

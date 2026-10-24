# Using Mu3Lab: what to expect

Mu3Lab gives you a collection of useful household apps on a computer you control. You can keep photos, files, recipes, paperwork, budgets, music and other personal information there, then reach them from your own devices. A shared home page helps you open the apps, see whether they are working and manage them. You can also use AI chat, including assistants that work with some of your apps.

This guide follows the full journey, from choosing a computer to recovering your information. For a shorter introduction, see the [README](../README.md). The [technical deep dive](architecture.md) explains the software behind it.

## Before deciding to install

**You are providing the computer and looking after it.** It must stay powered on, awake and connected whenever you want to use the apps from another device. A sleeping laptop or a power cut makes your home cloud unavailable. Applications may keep downloaded items available offline, but the Mu3Lab dashboard itself needs the server.

Mu3Lab is still being developed. Most routine work is guided, but setup can stop with an error that requires a terminal command or help from someone comfortable with Linux. It does not move all your information from existing services automatically. You still import your files, select photo albums, enter household records and set up phones inside the relevant apps.

You will need:

- A computer running Ubuntu 22.04 or newer, Debian 12 or newer, or a compatible Linux version the installer accepts. The current installer requires an **x86-64 processor**, the type used by many Intel and AMD computers. ARM computers, including Raspberry Pi models, are not currently accepted. This is not a normal Windows or Mac installer.
- A Linux account allowed to install software, and that account's password. Use your everyday account rather than signing in as the system administrator.
- An internet connection for setup, downloads, updates and any outside AI or web services you choose.
- A Tailscale account. Tailscale is the separate app that connects your approved devices privately. Review the account's terms and limits when signing up; Mu3Lab does not create or pay for the account.
- Space for the programs, your files, local backups and temporary copies during recovery. An app's displayed download size is not the total space your future library will use. Large photo libraries, games, audiobooks and local AI need substantially more space. There is no single memory or disk minimum that guarantees all the apps will run well together.
- Somewhere outside this computer to keep a backup copy and the recovery information. An external drive or another computer can serve this purpose; Mu3Lab does not arrange that copying for you.

A graphics card is optional. Supported NVIDIA cards can speed up some AI work if their drivers already work. Setup can add supporting software, but does not install the graphics driver itself. AMD acceleration is not fully automatic and is available only for some apps. Local AI can be slow without suitable hardware.

If this computer already runs other services, allow time for disruption: setup can install or upgrade Docker, the software that runs the apps, and change its settings and restart it. That can affect programs already using Docker. Setup also installs Tailscale, adds background services that continue after you log out, and may add the Bitwarden password-filling extension to supported browsers.

## 1. Download the project

Open the Linux **Terminal** application. It is the window where you paste commands and press Enter.

If Git, the program used to download Mu3Lab, is not installed, use:

```bash
sudo apt update
sudo apt install git curl tar coreutils
```

These preparation commands may ask for your computer password. Nothing appears while you type it; that is normal. Then run:

```bash
git clone https://github.com/dcazes/Mu3Lab.git
cd Mu3Lab
./install.sh
```

The first command creates a folder named `Mu3Lab` in your current folder. Keep it there after installation; Mu3Lab runs part of its software from this folder. Moving or deleting it can stop the dashboard and its background work.

Do not put `sudo` before `./install.sh`. The installer asks for the access it needs. Keep the terminal open while it works.

## 2. Give permission to install software

Setup asks for your computer password if Linux needs it. This allows system software to be installed. Mu3Lab does not save that password.

It first downloads its own setup tools, then checks the computer. If your Linux version or processor is unsupported, or another program occupies a connection it needs, setup stops and explains the problem. Resolve that message before trying again. These checks do not prove that the computer has enough capacity for every optional app.

## 3. Create your Mu3Lab account

For a new installation, enter your first name, email address and chosen password. The password must be at least 12 characters; setup asks you to repeat it and rejects using the email name in it. You must confirm that you have written it down safely.

This account becomes the first household administrator. The initial password also unlocks the password vault, called **Vaultwarden**. **Bitwarden** is the browser or phone app you use to view that vault and fill in saved passwords.

The dashboard sign-in and the vault are separate accounts even though setup gives them the same initial password. Do not assume that changing one changes the other. Keep track of both if you change either. Forgetting the vault password can leave its saved contents unrecoverable.

When resuming an existing installation, setup may ask for the existing vault login instead of creating another account. A rebuild from backups has a different path, described near the end of this guide.

## 4. Connect the server privately

Setup opens Tailscale's sign-in page. Sign in or create an account, then approve this computer. If the browser does not open, copy the address printed in the terminal into a browser and follow it. Setup continues when the connection is ready.

If Tailscale asks you to enable the private web-address feature, follow its displayed instructions. Account restrictions or an organization's Tailscale rules may require the person who manages that account to allow it.

You do not need to open your router to incoming internet traffic. However, knowing a Mu3Lab address is not enough to use it: each device must be connected through Tailscale and allowed to reach the server. Keep control of who can join that network.

## 5. Wait for the software to be prepared

Mu3Lab downloads and starts its password vault, shared sign-in and dashboard. The larger AI apps (the default chat app is **Open WebUI**) start downloading at the same time and carry on in the background after setup hands over to the dashboard. Most personal apps are optional and are installed later.

The time depends on your connection and computer; there is no fixed completion time. You can leave it working after answering the early questions, but must return for the final sign-in.

For Chrome, Chromium and Brave installations that setup recognizes, Mu3Lab adds Bitwarden and points it at your vault. You may see **“Managed by your organization”** in the browser because of the setting used to add it. Mu3Lab sets the extension and its vault address; this does not mean a workplace has taken over your browser. While that setting is present, the extension can be disabled but not removed normally. Restart the browser if it does not appear. Other browsers and other devices need manual extension setup.

If setup fails, read the message and keep the log location printed in the terminal. Return to the same Mu3Lab folder and run `./install.sh` again after addressing the problem. It checks completed work before continuing. Re-running it does not download a new version of the project automatically.

## 6. Sign in to finish installation

Setup opens, or prints, your private dashboard address. It normally ends with `:8446/`; keep the entire address, including that part, when copying or bookmarking it.

Sign in with the Mu3Lab email and password you chose. **The terminal waits until you sign in and the dashboard successfully loads.** A message saying it is waiting for your sign-in is not a frozen installer. If you left during downloads, this is the step that needs you to return.

Setup ends once the dashboard loads, even if the AI apps are still downloading. Home shows a **Setting up your AI apps** card with each app's progress, and you can start on the Get started steps meanwhile; chat becomes available when they finish. If one does not finish, Home says so and **Settings → System** has a button to try again. A successful dashboard sign-in does not mean you have already connected an AI provider, imported content or set up backups.

## 7. Finish the Home checklist

Home shows the next steps and remembers your progress across devices. Some items are detected automatically; others are yours to mark complete. Hiding or completing the checklist is not a backup check or a promise that every app has been configured.

### Set up password filling

Click the Bitwarden shield in the browser and sign in to the vault. If Mu3Lab added it, the server address is already filled in.

If you install Bitwarden yourself, choose **Self-hosted** on its sign-in screen, copy the vault address from Mu3Lab's instructions, save it, then sign in. Do not use the dashboard address or the ordinary Bitwarden website as your vault server.

Mu3Lab saves generated app logins automatically, but it can take a little time for them to appear and sync to your device. Check **Settings → Sign-in** if saving is stalled. A manual “save logins” action can ask for the vault password again, and for a two-step sign-in code if you enabled one. That password is used for the request, not saved for future use.

### Choose how AI should work

In **Settings → AI**, an administrator chooses the household's AI setup.

For an outside provider, follow a provider link, create an account there, complete its verification steps and copy the access key it gives you into Mu3Lab. An access key is a private code that lets Mu3Lab use your account. Mu3Lab checks the key before reporting the connection as working. It does not finish the provider's registration for you. Some providers require payment details or offer only a limited trial. Free allowances, availability and any charges are controlled by that provider; do not assume unlimited free use.

During setup, Mu3Lab measures this computer's memory and graphics card, leaves room for the apps you have installed (and the ones that use the graphics card), and suggests the largest reviewed local model that still fits. If you accept, it downloads after setup and chat answers on your computer first, with outside providers as a choice in chat for harder questions. If no local model fits, setup suggests connecting an outside provider instead. You can always change this in Settings, then AI.

In Settings, then AI, turn on **Local models**, **Outside providers** or both, under "Where chat runs":

- **Local models** answer privately on this server with the chat model you choose.
- **Outside providers** answer through the providers you connect. Messages and any app information included in a conversation may be sent to that company. Being on your private home cloud does not prevent that.

With both on, the one marked **First** answers in every app; use **Move up** to change the order. The second is a choice in chat's model menu, for example outside providers for a harder question. It never answers on its own when the first fails, so a private chat is never sent outside without someone choosing to. With only local models on, chat cannot answer if the local model stops working.

Each local model is marked **Runs well here**, **Slow here** or **Too big for this computer now**, judged against what your other apps need, and **Suggested** marks the best fit. **Try it** asks a downloaded model (or your outside providers) one test question and shows the answer and how fast it came. Models you stop using stay downloaded, so switching back is instant; **Remove** frees a model's disk space.

Turning outside providers off does not stop web searches, app downloads, map downloads or other apps' outside connections. Changing the model used to search documents can require processing the documents again; some installed research apps prevent that change.

### Install personal apps

Open **Apps → Discover**, choose what you want, fill any required settings and review the installation plan. It shows downloads and space estimates. Programs can download together, while setup work is handled in order. Keep enough extra room for your content and backups.

You can leave the dashboard and return to see progress. An app may need a first visit, an account step or attention before it is usable. Open its page and follow its setup and device instructions. “Installed” does not mean your own files or records have been added.

## 8. Know what each optional app still needs from you

| App | What you get and what you still do |
| --- | --- |
| Immich | A photo and video library. Install its phone app if wanted, enter the private address, sign in, grant photo access and choose what to upload. Existing phone photos do not arrive just because the server app was installed. |
| Nextcloud | Files, calendars and contacts. Upload files and connect each computer or phone you want to sync. The Mu3Lab calendar connection is a separate choice, described below. |
| Paperless-ngx | Searchable paperwork. Import documents or arrange scanning. A phone client may ask you to create and paste a private access code from your Paperless profile. |
| Outline | A household wiki and notes. Create collections and documents and choose who can see them. |
| Mealie | Recipes and meal plans. Import or enter recipes and make your plans and lists. |
| Grocy | Pantry stock and household lists. Check the currency setting, then add products, storage locations and quantities. |
| Actual Budget | Budget planning. Open it to establish your account, then create or import a budget. Its chat connection asks for an “Actual server password” and “Budget Sync ID” (the identifier of the budget to use); Mu3Lab cannot fill these in automatically. If your sign-in setup does not provide the required password, that connector cannot be assumed to work. This is not a bank account connection or automatic import of financial records. |
| Baby Buddy | Shared baby-care tracking. Add the children and records you want to track. Admitted household users receive caregiver access; do not treat it as a separate private diary for each person. Phone apps can require the app's “Add a Device” step. |
| Beaver Habits | A private habit list for each person. Add your habits and tick them off yourself. |
| AdventureLog | Travel journals and planning. Add places, trips and collections. |
| Dawarich | Location history. Select a map region, connect a supported location-recording phone app and grant the phone permissions it needs. Map data downloads separately; the catalog estimates roughly 15 GB for Canada's map index and warns first setup can take up to an hour. Larger regions need more. History recording is not enabled on your phone automatically. |
| Navidrome | Music streaming. Supply your music; with Nextcloud installed, use the shared Music folder. Otherwise copy music into `/srv/mu3lab/data/navidrome/music` on the server, getting help with folder access if needed. Browser sign-in is automatic, but music phone apps use the separate Navidrome login saved in your vault. The music library is included in backups. |
| Audiobookshelf | Audiobooks and podcasts. Add your books under `/srv/mu3lab/media/audiobooks`, organized into folders, and manage podcast subscriptions in the app. You may need help copying files into this server folder. These books and downloaded podcasts are **not** in Mu3Lab backups. The catalog provides an Android client and a browser option for iPhone; use the current device instructions. |
| RomM | A library and player for game files you supply. Copy your files into `/srv/mu3lab/media/roms`, one folder per system, then scan the library in RomM. Optional cover-art and achievements services need their own accounts or access keys. Games are not provided. Game files are **not** backed up; saved progress and app data are. Not every system plays in every browser. |
| SurfSense | A research workspace. Pass the Mu3Lab sign-in, then use the separate SurfSense login saved in your vault. Add sources and documents yourself. Web research may contact outside services. |
| Firecrawl | Web-page reading tools for AI. It has no normal personal-app screen to open. It can use substantial memory while working. |
| LobeChat | An alternative chat app. Install it, choose it in **Settings → AI**, then follow its one-time connection approval for each person who uses its app assistants. It keeps its own conversations; selecting it does not migrate Open WebUI history. |
| Speech | Local speech recognition and spoken answers. Wait for its downloads, choose a voice and allow microphone access in chat. It has no standalone screen. Without this app, browser voice behavior can differ and should not be assumed to use the server's private speech service. |
| Kopia backups | Scheduled backups on this computer. Install it, choose a schedule, save the recovery sheet and arrange your own off-computer copy. Details below. |

Apps discussed in planning documents, but absent from Discover, are not currently available to install through Mu3Lab.

## 9. Connect phones and other computers

On each device, install Tailscale, join the same private network with an authorized account, and connect. Open your saved Mu3Lab dashboard address and sign in. A household Mu3Lab invitation does not automatically give the device Tailscale access.

For each app you want on that device, open its Mu3Lab page's device instructions. They provide the relevant download link, server address and sometimes a square code to scan. Some apps use the browser; others use a separate phone app. Some clients are community projects rather than official apps and may have their own limits or costs.

Enter the exact app address provided, including any number at the end. You may need to approve a browser sign-in, copy a device code, grant photo or microphone permissions, or use a separate saved login. A link or square code alone does not complete every client's setup.

Adding the dashboard to a phone's home screen makes it easier to open. It does not make the server work offline. Tailscale normally needs to be connected when syncing or reaching the apps away from home.

## 10. Invite your household

An administrator opens **Settings → People**, adds a person's name and email, and selects member or administrator. Mu3Lab shows an invitation link for you to send; it does not send the message for you. The link works once and has an expiry time. The recipient opens it on an authorized device, signs in, and chooses a password in Authentik's **Settings → Change password**.

Members can use household apps and their own calendar. Administrators can install and remove apps, manage people, configure AI and access backup administration. Only give administrator access to someone you trust with the server and its information.

A new person's password vault needs its own account with the matching email. Do not assume the household invitation created it. Follow the sign-in/vault setup shown for that person; generated logins are saved once the matching vault account is available.

Apps have their own sharing rules. Some provide separate private information; others are shared household tools. Chat assistants are available to household members too, and may use shared app access that reaches more than the person's own private records. Review that before inviting someone.

Deactivating someone starts removal of the access Mu3Lab manages. Previously downloaded files, an offline password-vault copy and access codes issued directly by another app are not all erased by this action. Review those apps and Tailscale access as well when removing someone from the household.

## 11. Use chat, calendars and voice

**Chat:** select the app assistant you need. Only apps with a working connection have useful app tools. An administrator controls those tools under **Settings → Chat integrations**, which links to **ContextForge**, the tool-management app. **Tools that are enabled can make changes without a separate confirmation screen.** An assistant's promise to ask first is not an enforced safeguard. Turn off tools you do not want it to use. Check important results in the app itself; AI answers can be wrong.

**Calendar:** you can create and edit personal events in Mu3Lab before installing Nextcloud. Those events appear in Mu3Lab on your connected devices, but do not automatically appear in your phone's calendar app. To use Nextcloud, install and open it, then use **Connect calendar** in the Calendar settings, approve the new browser page and choose the calendar to display. Use the offered sync action to send locally kept events; if the same event changed in both places, choose which version to keep. Disconnecting tries to keep a local copy. If Nextcloud is unavailable, the connection can still be removed without copying its events or confirming that its access code was withdrawn; those events remain in Nextcloud. Check that your events are present before relying on the disconnected calendar. A temporary connection failure is not permission to assume that all recent events have synced.

**Voice assistant:** with LiveKit installed, open **Talk** and speak. It knows who you are from your Mu3Lab sign-in. When you ask about an app ("what's on my calendar tomorrow?"), it hands you to that app's assistant, which uses your own access, exactly like chat, and hands you back when you are done. Before it adds, changes or deletes anything it says what it will do and waits for you to say yes; this check is enforced, not just promised, and each yes allows one change. An administrator chooses which app assistants voice may use under **Settings → Voice assistant**.

**Voice on another device or program:** after Speech is installed, **Settings → Voice key** lets you create your own private access code. Copy it when shown; it is shown only once. Enter it and the displayed address in a compatible program, following that program's setup. Keep it private, and replace or remove it if lost. This does not automatically configure a microphone or another home-automation product.

## 12. Protect your information

Install **Kopia backups** from Discover to get scheduled backups. In **Settings → Security & backups**, choose a schedule: off, every six or twelve hours, daily or weekly. The default is Sunday at 03:30 in the server's local time. Confirm that the server clock and time zone are correct.

During a backup, each app is briefly stopped while its information is copied, then restarted. The duration depends on how much data it has. A busy app may be skipped and reported. If the server was off, the backup service can catch up when it returns. Check the backup status rather than assuming that a missed or skipped run succeeded.

Backups include Mu3Lab's settings and passwords needed for recovery, the supported core apps, and included personal-app data. They do **not** include the audiobook, podcast and game libraries in `/srv/mu3lab/media`. Downloaded AI models and certain downloaded map data are also excluded and must be fetched again. Keep a separate copy of your media libraries.

Open the **recovery sheet**, save or print it, and keep it away from the server. Its backup password is different from your sign-in password. A copy is also saved to administrator vaults, but a vault available only on the failed server will not help after that server is lost.

The encrypted backup folder is `/srv/mu3lab/backups/kopia`. **It is on the same computer as your data.** Copy it to another device or drive and refresh that copy regularly. The recovery sheet alone contains no backup data; a copied backup folder without its password cannot be opened. The project does not currently automate off-device backups.

Older backups are eventually removed according to the backup service's retention rules: the newest ten, plus daily, weekly and monthly history. They are not an unlimited permanent archive. Backup checks can find some damage but do not replace verifying that you can recover what matters to you.

## 13. Updates, interruptions and recovery

**App updates:** an administrator applies the approved version offered on the app's page. Mu3Lab downloads it, stops the app, backs up its data, then starts the new version. That pre-update backup happens even without the scheduled backup app, but is not a replacement for regular full backups. If startup fails, Mu3Lab attempts to restore the previous version and data. Leave time for downtime, and check the app afterwards.

**Mu3Lab updates:** use **Settings → System → Mu3Lab updates** when available. Automatic updates require an eligible release installation without local edits. A development copy, or an update needing computer-administrator permission, requires updating the project in the terminal and running `./install.sh`. Follow the displayed instructions; simply re-running the installer uses the source already on disk. The dashboard restarts during a platform update. Installed optional apps keep their versions until you update them separately.

**Restore one app:** on its Backups card, choose **Restore**, select the saved copy and type the requested app name. Restoring replaces the app's current information with that older copy, so later changes can be lost. It may restore the older app version too. Extra free disk space is needed while the saved copy is unpacked beside the current one. Mu3Lab stops the app for the change and attempts to put the original data back if the restore fails.

**An interrupted update or restore:** after a power loss, Mu3Lab may deliberately stop and ask for a recovery choice. Follow **Put my original data back**, **Undo update**, or **Keep new version** when offered. Do not expect every interrupted action to continue unattended. If recovery itself fails, keep the files and error details for diagnosis rather than repeatedly deleting or reinstalling.

**A lost computer:** obtain the copied backup folder and recovery sheet. Download Mu3Lab onto a compatible replacement computer and run `./install.sh --restore` from its folder. Supply the copied backup folder's location and backup password, and check the code shown against the sheet. Setup also needs administrator permission and Tailscale approval. It restores the saved accounts; sign in with your old account instead of creating a new one. Restore your separately saved media yourself, reconnect devices if addresses changed, and check apps, calendars and AI connections. Some authorizations must be renewed. A rebuild uses the available backups, so different apps can return to different saved times.

If that rebuild is interrupted, use `./install.sh --restore` again with the same backup source. Do not start an ordinary fresh install over the partially restored information. If setup refuses because files changed or it cannot establish what is safe to replace, preserve the files and seek technical help. The [recovery reference](backup-recovery.md) covers manual recovery.

## 14. Everyday checks and common surprises

Home shows whether apps need attention and how much memory and disk the server is using. Status is checked periodically, so a recently started or stopped app can take time to update. “Status may be out of date” means the dashboard cannot confirm the current situation; it is not a success message.

| What you see | What to do |
| --- | --- |
| The dashboard will not open | Check that the server is awake and connected, Tailscale is connected on your device, and you copied the whole address. |
| Setup is waiting for a dashboard login | Open its printed address, sign in and let Home finish loading. |
| An app asks for another password | Read that app's instructions and check Bitwarden. Some apps and phone clients have their own login. |
| Chat opens but cannot answer | Check the selected local model or provider connection in Settings → AI. Installation alone does not provide an outside account. |
| An app assistant is missing or cannot work | Check that the app and its chat connection are running and the tools are enabled. LobeChat may need your connection approval. |
| A phone is not uploading photos or recording locations | Check the phone app's server address, sign-in, selected content, permissions and Tailscale connection. |
| Disk space is low | Review resource use and the cleanup choices under System. Account for your library and backups as well as programs; recovery needs spare room. |
| A job fails | Open its details, read the suggested next step and save the error before retrying. A retry is not proof that the earlier action made no changes. |

## 15. Removing an app or removing everything

Removing one app in the dashboard offers data choices. Keeping data lets a later reinstall reuse it; deleting data removes that app's owned stored information. Shared folders and the large media libraries have different handling. Read the confirmation rather than assuming that every related file disappears. Removing an app is not the same as erasing all its old backups.

To see what full removal would do, open Terminal in the Mu3Lab folder and run:

```bash
./uninstall.sh --dry-run
```

To actually remove Mu3Lab, run `./uninstall.sh` and type `DELETE` when asked. **Full removal deletes all of `/srv/mu3lab`: app files, accounts, the vault, local backups and media libraries.** Copy anything you want to keep to another location first. Media preserved during an individual app removal is not preserved by this full-removal script.

The `--everything` option also removes Docker and everything stored in it, including unrelated programs, as well as Tailscale and NVIDIA container support. It is a much broader removal. The script keeps the project source folder, system Python and graphics drivers. It removes Mu3Lab's browser-extension setting; restart the browser to complete that change.

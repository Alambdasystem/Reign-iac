# REIGN Node for Android

The pilot bundles SmolLM2 360M Instruct (Q4_K_M) from Hugging Face and runs
llama.cpp through wllama 2.3.7 in a local WebView. The handset can answer
offline even before enrollment. Local prompts stay in the app. **Ask REIGN
Core** sends only the text in the message box over the existing authenticated
AX chat API. **Edit answer as next message** lets the user choose what to
send between models.

1. Install the signed APK on Android 9 or newer with an up-to-date Android
   System WebView. Open **REIGN Node → Load local model**.
2. Enter a question and tap **Ask phone**. Airplane mode works with the bundled
   runtime and model.
3. For cloud features, open **Open Genesis / sign in**, sign in to REIGN, and
   tap **Return to REIGN Node**. Tap **Register this phone** once.
4. Open **Genesis → Distributed Nodes** to see the installation. The existing
   node label identifies the app and model. With the matching AX/Core update,
   the loaded foreground app reports app ID, version, model, and runtime every
   30 seconds. An idle phone goes offline after the registry's 90-second TTL.
   **Disable** in the console revokes check-ins. A disabled device cannot revive itself.

The console and local app use separate WebViews. Only the bundled, local UI
has the native interface. The node-scoped credential is stored in the
application's private Android storage, excluded from backup. AX forwards only
that credential to the private heartbeat route. Heartbeats do not carry prompts.
Records describe enrolled REIGN apps, not an inventory of unrelated Android apps.
Remote task dispatch is still disabled.

## Rebuild

Use Python 3.11+, Android SDK 36, JDK 17 or 21 and Gradle 8.14.3.
In this directory run `python3 prepare_assets.py`, then
`gradle assembleDebug` for a local build, or `gradle assembleRelease` and
sign its output with the deployment's Android signing key.
The asset preparation script verifies both upstream SHA-256 checksums.
Model and runtime licenses ship in the APK.

# TestrayAutomatedTasks

Scripts to run automation between **Liferay**, **Testray**, and **Jira**.

---

## ✅ Dependencies

### 1. Install Python (if not already installed)

<details>
<summary>Mac</summary>

```bash
brew install python
```

</details>

<details>
<summary>Linux (Ubuntu/Debian)</summary>

```bash
sudo apt update && sudo apt install python3
```

</details>

<details>
<summary>Fedora</summary>

```bash
sudo dnf install python3
```

</details>

---

### 2. Install `pip`

<details>
<summary>Mac</summary>

If you installed Python via Homebrew, `pip` is already included.

</details>

<details>
<summary>Linux (Ubuntu/Debian)</summary>

```bash
sudo apt install python3-pip
```

</details>

<details>
<summary>Fedora</summary>

```bash
sudo dnf install python3-pip
```

</details>

---

### 3. Install `jira-cli`

This is required to check Jira ticket statuses and for future Jira automation features.

#### Install globally using npm:

```bash
sudo npm install -g jira-cli
```

#### Configure `jira-cli`:

```bash
jira config
```

🔗 More info: [npmjs.com/package/jira-cli](https://www.npmjs.com/package/jira-cli)

---

## 📦 Clone the Repository

Clone this repo into your preferred working directory:

```bash
gh repo clone magjed4289/TestrayAutomatedTasks
```

---

## ⚙️ Configure Environment Variables

1. Move the `.automated_tasks.env` file **outside** and **at the same level as** the `TestrayAutomatedTasks` directory.

2. Edit `.automated_tasks.env` and fill in the required variables.

**Your file tree should look like this:**

```
your-workspace/
├── TestrayAutomatedTasks/
└── .automated_tasks.env
```

---

## 🔐 Jira API Token Setup

1. Go to your **Jira Profile** → **Manage Your Account** → **Security** tab.
2. Under **API tokens**, click **Create and manage API tokens**.
3. Generate a new token and **copy it**.

### 🖥️ Setup on Your Machine

4. In your home directory (`~`), create a hidden folder named `.jira-user`:

   ```bash
   mkdir ~/.jira-user
   ```

5. Inside the `.jira-user` folder, create two files (no file extensions):

    - `token`: Paste your copied API token into this file.
    - `user`: Enter the email address associated with your Jira account.

   Your folder structure should look like this:

   ```
   ~/.jira-user/
   ├── token
   └── user
   ```

### ⚙️ What Happens on First Run

- When you run the automation script for the first time:
    - Your **API token will be securely encrypted** for future use.
    - The plain `token` file will be **automatically deleted** after encryption.

---

## 📥 Install Python Dependencies

From the root of the `TestrayAutomatedTasks` directory:

```bash
pip install -r requirements.txt
```

---

## ▶️ How to Use

Run the script from the correct subdirectory:

```bash
cd /home/me/Projects/eng/TestrayAutomatedTasks/liferay/teams/headless
python3 headless_testray.py
```

---

## 🧠 Optional: Add an Alias for Convenience

To avoid typing the full path every time, you can add an alias in your `.bashrc` or `.zshrc`:

```bash
alias rta="cd /home/me/Projects/eng/TestrayAutomatedTasks/liferay/teams/headless && python3 headless_testray.py"
```

After saving, apply the changes:

```bash
source ~/.bashrc
# or if using zsh
source ~/.zshrc
```

Now you can run the automation with:

```bash
rta
```

---

## 🪪 License

[MIT License](https://choosealicense.com/licenses/mit/)


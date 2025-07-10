# nearby
tg bot to monitor near validator

### Installation
 1. Create telegram bot and get Api Token with @BotFather.
 2. Clone bot to server
```sh
cd $HOME && git clone -v https://github.com/ama31337/serverbot.git && cd ./serverbot && chmod +x ./installsbot.sh
```
 3. Open ./config.py and insert your bot API, telegram id, pool name and alert settings
 4. Run script ./installsbot.sh for Ubuntu/Debian, source your bash to make bot start/stop commands working
 5. Send to your new bot command /start
```sh
./installsbot.sh
```
```sh
source ${HOME}/.bashrc
```

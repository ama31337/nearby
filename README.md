# nearby
tg bot to monitor near validator

### Installation
 1. Create telegram bot and get Api Token with @BotFather.
 2. Clone bot to server
```sh
cd $HOME && git clone -v https://github.com/ama31337/nearby.git && cd ./nearby && chmod +x ./installsbot.sh
```
 3. Open env.py and insert your bot API, telegram id, pool name and alert settings
 4. Run script ./installsbot.sh
```sh
./installsbot.sh
```
```sh
source ${HOME}/.bashrc
```
 5. Send to your new bot command /start

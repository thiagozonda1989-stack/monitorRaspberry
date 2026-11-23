# monitorRaspberry

Versão 0.1.0. Monitor de gabinete com Raspberry Pi, duas temperaturas, tensão da bateria e tensão da entrada CA. O Pi lê a cada 30 segundos por padrão, registra em SQLite e envia a fila por HTTPS quando a conexão via ZTE LTE está disponível. O servidor preserva a hora original de medição e elimina reenvios pelo ID da leitura.

| Canal | Aquisição |
|---|---|
| Temperaturas interna e externa | Dois DS18B20 no mesmo barramento 1-Wire |
| Bateria nominal 12 V | Divisor e ADS1115, faixa de projeto de 0 a 30 Vcc |
| Entrada 110/127 ou 220 Vca | Medidor DIN SDM120-M via RS-485 isolado |

Veja [circuitos, pinagem, lista de materiais e comissionamento](docs/hardware.md) antes de energizar. O painel apresenta as leituras, gráficos, horário de medição e recebimento em hora de Belém e exportação CSV. Uma leitura ausente é gravada como nula, com o erro associado.

## Servidor HTTPS

Em um servidor Linux com Docker Compose, aponte um DNS público para ele e libere TCP 80 e 443 para o Caddy. Clone o repositório no servidor e crie o arquivo `.env` na raiz, com permissão 600.

```dotenv
MONITOR_DOMAIN=monitor.seudominio.com.br
UPLOAD_TOKEN=cole_um_token_exclusivo_de_envio
READ_TOKEN=cole_outro_token_para_leitura
```

Gere cada token com `openssl rand -hex 32`. Use o token de envio apenas no Raspberry. O token de leitura é inserido no painel pelos operadores.

```bash
docker compose up -d --build
docker compose logs --tail=50 app caddy
```

Acesse `https://monitor.seudominio.com.br/`. A API fica no mesmo domínio. O volume `telemetry` guarda o SQLite do servidor. Para backup consistente, use a API SQLite de backup ou pare a aplicação antes de copiar o volume. Monitore espaço em disco. Esta versão mantém as leituras sem expurgo automático nos dois bancos.

## Raspberry Pi

1. Com Raspberry Pi OS, conecte o modem ZTE à rede da operadora. Se ele for roteador, prefira Ethernet ou Wi-Fi. Se for USB, configure a interface de rede ou a conexão móvel pelo NetworkManager, conforme o modelo, SIM e APN. Verifique DNS e HTTPS saindo do Pi. O coletor usa a rota padrão e funciona atrás de CGNAT.
2. Na raiz do repositório, rode `sudo bash deploy/install-pi.sh` e reinicie o Pi para ativar I²C e 1-Wire.
3. Identifique cada sonda com `ls /sys/bus/w1/devices/28-*` ou `ls /sys/bus/w1/devices` e o adaptador com `ls -l /dev/serial/by-id/`. Valide a identidade de cada sonda aquecendo uma por vez. Edite `/etc/monitorraspberry/config.json` com `sudoedit`. Defina `device` único por gabinete, IDs ROM, porta serial, URL HTTPS e token de envio.
4. Reinicie os serviços e acompanhe o journal.

```bash
sudo systemctl restart monitorraspberry@collect.service monitorraspberry@sync.service
sudo journalctl -f -u monitorraspberry@collect.service -u monitorraspberry@sync.service
```

As duas unidades iniciam no boot. A coleta independe da conexão com o servidor. Em caso de erro de envio, o serviço repete a tentativa com espera progressiva de até cinco minutos. Os registros são mantidos localmente mesmo após a confirmação. Use um relógio confiável quando o Pi precisar iniciar sem acesso ao NTP.

Para exportar todas as amostras locais, inclusive as que aguardam sincronização:

```bash
sudo -u monitorraspberry python3 /opt/monitorraspberry/monitor.py csv /etc/monitorraspberry/config.json > leituras.csv
```

## Teste sem hardware

`python3 -m unittest discover -s tests -v` verifica fila offline, reabertura do banco local, envio por HTTP loopback, reenvio idempotente, autenticação, histórico, arquivos do painel e exportação CSV.

Para demonstração, rode `python3 server.py` com `READ_TOKEN`, `UPLOAD_TOKEN` e `SERVER_DB` definidos no ambiente. Em uma cópia da configuração, coloque `demo: true`, aponte `server_url` para `http://127.0.0.1:8080` e use um banco local em caminho gravável e identificador dedicado à demonstração. Execute `python3 monitor.py collect caminho-da-config.json` e `python3 monitor.py sync caminho-da-config.json` em terminais separados. Abra o endereço local e forneça o token de leitura.

# Локальная проверка ЭЦП НУЦ

SIGEX используется только для обмена с eGov Mobile. Подписи XML входа и CMS согласия проверяются на сервере: целостность документа, ИИН, срок и назначение сертификата, цепочка НУЦ и подписанный ответ OCSP с nonce. При недоступном OCSP доступ не выдаётся; полученная подпись остаётся для повторной проверки.

Проверка основана на алгоритмах `MobileXmlVerifier`, `MobileCmsVerifier` и `EdsVerifier` проекта doctorCabinet. Используются Java 17+, SDK НУЦ и Bouncy Castle. Секретные ключи для проверки не нужны. Подписи передаются дочернему процессу через stdin и не попадают в аргументы или журналы. Во внешний сервис НУЦ передаётся только OCSP-запрос статуса сертификата, без документа и подписи.

В `lib/` перед сборкой разместить библиотеки из используемого комплекта doctorCabinet/SDK:

- `knca_provider_jce_kalkan-0.7.5.jar`, `kalkancrypt-xmldsig-0.5.jar`;
- `xmlsec-3.0.6.jar`;
- `bcprov-jdk15on-1.70.jar`, `bcpkix-jdk15on-1.70.jar`, `bcutil-jdk15on-1.70.jar`;
- `slf4j-api-1.7.36.jar`, `commons-codec-1.18.0.jar`.

SDK и сборка исключены из Git. Публичные сертификаты доверия НУЦ находятся в `trust/`; пользовательские сертификаты туда добавлять нельзя.

Из каталога backend:

```sh
mkdir -p verifier/classes
javac --release 17 -encoding UTF-8 -cp 'verifier/lib/*' -d verifier/classes verifier/Verifier.java
```

`EDS_JAVA_COMMAND` задаёт путь к Java, по умолчанию `java`. Docker компилирует модуль при сборке. Для systemd готовые библиотеки и классы включаются в пакет релиза. `CONSENT_SIGNATURE_REQUIRED=false` временно разрешает ручную отметку согласия врачом, не отключая проверку ЭЦП входа.

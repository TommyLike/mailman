Mailman Configuration
=====================

This is Mailman's default configuration, directly included from the source
code. You can override these in your configuration to modify the behavior of
Mailman Core. `schema.cfg` includes templates for several configuration options
that are instantiated inside of `mailman.cfg`. Configuration options provided in
`mailman.cfg` override those provided in `schema.cfg`.


.. literalinclude:: ../schema.cfg
.. literalinclude:: ../mailman.cfg

<div class="table-wrap">
  <div style="padding:12px">
    {#each { length: 6 } as _, i}
      <div class="sk" style="width:{60 + ((i * 13) % 40)}%;height:16px"></div>
      <div style="height:14px"></div>
    {/each}
  </div>
</div>
